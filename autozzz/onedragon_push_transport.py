"""Constrain the fixed SDK's HTTP calls while delivering a managed notification."""
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Lock, RLock
from urllib.parse import urlsplit

import requests
from one_dragon.base.push.channel import discord, telegram, webhook, work_weixin_app, work_weixin_bot

from .onedragon_notify import credential_fragments


_CHANNEL_MODULES = dict(DISCORD=discord, TG=telegram, WEBHOOK=webhook,
                        QYWX_APP=work_weixin_app, QYWX=work_weixin_bot)
_ACTIVE = ContextVar("autozzz_push_transport", default=None)
_MODULE_LOCK = Lock()


class ManagedWorkWeixinApp(work_weixin_app.WorkWeixinApp):
    def __init__(self):
        super().__init__()
        self._access_token_lock = RLock()
        self._token_credentials = None

    def _get_access_token(self, corpid, corpsecret, proxies):
        credentials = corpid, corpsecret
        with self._access_token_lock:
            if credentials != self._token_credentials:
                self._last_access_token_info = "", 0.
                self._token_credentials = credentials
            return super()._get_access_token(corpid, corpsecret, proxies)


class _Delivery:
    def __init__(self, channel_id, module):
        self.channel_id, self.module = channel_id, module
        self.secrets = set()
        self.message_attempted = False

    def request(self, name, send, *args, **kwargs):
        url = kwargs["url"] if "url" in kwargs else args[1 if name == "request" else 0]
        prepared = requests.PreparedRequest()
        prepared.prepare_url(url, kwargs.get("params"))
        self.secrets.update(credential_fragments((url, prepared.url)))
        is_app_message = self.channel_id == "QYWX_APP" and urlsplit(prepared.url).path.endswith("/cgi-bin/message/send")
        if is_app_message:
            if self.message_attempted:
                raise requests.RequestException("原消息送达状态未核实，未重新发送。")
            self.message_attempted = True
        kwargs["allow_redirects"] = False
        response = send(*args, **kwargs)
        if 300 <= response.status_code < 400:
            response.close()
            raise requests.HTTPError(f"通知接口返回 HTTP {response.status_code} 重定向，未继续发送。")
        if is_app_message and 200 <= response.status_code < 300:
            parse = response.json

            def checked_json(*args, **kwargs):
                data = parse(*args, **kwargs)
                code = data.get("errcode") if isinstance(data, dict) else None
                # 图文回退只接受服务端明确拒绝原消息的响应。
                self.message_attempted = not (type(code) is int and code != 0)
                return data

            response.json = checked_json
        return response


class _ScopedRequests:
    def __init__(self, module, original):
        self.module, self.original, self.users = module, original, 0

    def __getattr__(self, name):
        original = getattr(self.original, name)
        if name not in ("get", "post", "request"):
            return original

        def send(*args, **kwargs):
            delivery = _ACTIVE.get()
            if delivery is None or delivery.module is not self.module:
                return original(*args, **kwargs)
            return delivery.request(name, original, *args, **kwargs)

        return send


@contextmanager
def delivery_transport(channel_id):
    module = _CHANNEL_MODULES.get(channel_id)
    if module is None:
        yield
        return
    with _MODULE_LOCK:
        shim = module.requests
        if not isinstance(shim, _ScopedRequests):
            shim = module.requests = _ScopedRequests(module, shim)
        shim.users += 1
    token = _ACTIVE.set(_Delivery(channel_id, module))
    try:
        yield
    finally:
        _ACTIVE.reset(token)
        with _MODULE_LOCK:
            shim.users -= 1
            if not shim.users:
                module.requests = shim.original


def delivery_secrets():
    delivery = _ACTIVE.get()
    return () if delivery is None else tuple(delivery.secrets)
