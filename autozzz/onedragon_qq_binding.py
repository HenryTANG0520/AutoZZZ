"""运行实际 QQ 绑定协议；保留唯一作业、配置版本和取消后的原线程。"""
from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime
import threading
import time
import importlib.util
from urllib.parse import unquote, urlsplit
import uuid

from .config_transaction import atomic_apply, config_revision
from .onedragon_notify import redact
from .run_lock import profile_lock

_ACTIVE = ContextVar("autozzz_qq_binding", default=None)
_PATCH_LOCK = threading.Lock()


class BindingCancelled(RuntimeError):
    pass


class GatewayTransport:
    """只在本绑定线程替换模块依赖；解析、心跳与匹配仍由固定 SDK 执行。"""
    def __init__(self, module, cancelled, deadline, proxy, protect):
        self.module, self.cancelled, self.deadline = module, cancelled, deadline
        self.proxy, self.protect = proxy, protect
        self.ws, self.ws_lock = None, threading.RLock()
        self.originals, self.token = {}, None

    def check(self):
        if self.cancelled.is_set(): raise BindingCancelled("已取消绑定。")
        remaining = self.deadline - time.monotonic()
        if remaining <= 0: raise TimeoutError("绑定超时，请重新绑定。")
        return remaining

    def __enter__(self):
        if not _PATCH_LOCK.acquire(blocking=False): raise RuntimeError("原 QQ 连接尚未退出。")
        try:
            self.originals = {name: getattr(self.module, name) for name in ("requests", "websocket", "time")}
            transport = self
            class Dependency:
                def __init__(self, original): self.original = original
                def __getattr__(self, name): return getattr(self.original, name)
            class Http(Dependency):
                def post(self, url, **kwargs): return self.request("post", url, **kwargs)
                def get(self, url, **kwargs): return self.request("get", url, **kwargs)
                def request(self, method, url, **kwargs):
                    if _ACTIVE.get() is not transport: return getattr(self.original, method)(url, **kwargs)
                    if (method, url) not in {("post", transport.module.QQBot._TOKEN_URL), ("get", transport.module._GATEWAY_URL)}:
                        raise RuntimeError("QQ 绑定不允许发送聊天消息。")
                    remaining = transport.check()
                    for value in kwargs.get("headers", {}).values():
                        if isinstance(value, str) and value.startswith("QQBot "): transport.protect(value.removeprefix("QQBot "))
                    kwargs.update(timeout=min(float(kwargs.get("timeout", 15)), remaining), allow_redirects=False,
                                  proxies={"http": transport.proxy or "", "https": transport.proxy or "", "all": ""})
                    response = getattr(self.original, method)(url, **kwargs)
                    # 防止错误响应中意外附带令牌，随后被上游错误文本或异常日志打印。
                    if method == "post":
                        try:
                            data = response.json()
                            if isinstance(data, dict) and data.get("access_token"): transport.protect(str(data["access_token"]))
                        except (ValueError, AttributeError): pass
                    transport.check()
                    return response
            class Clock(Dependency):
                def sleep(self, seconds):
                    if _ACTIVE.get() is not transport: return self.original.sleep(seconds)
                    remaining = transport.check()
                    transport.cancelled.wait(min(seconds, remaining))
                    transport.check()
            class Sockets(Dependency):
                def create_connection(self, url, **kwargs):
                    if _ACTIVE.get() is not transport: return self.original.create_connection(url, **kwargs)
                    remaining = transport.check()
                    parts = urlsplit(url)
                    if parts.scheme != "wss" or not parts.hostname or parts.username or parts.password:
                        raise ValueError("QQ 返回的网关地址无效。")
                    kwargs.update(timeout=min(float(kwargs.get("timeout", 10)), remaining))
                    if transport.proxy:
                        proxy = urlsplit(transport.proxy)
                        kwargs.update(http_proxy_host=proxy.hostname, http_proxy_port=proxy.port or (80 if proxy.scheme == "http" else 1080),
                                      proxy_type=proxy.scheme, http_no_proxy=[""],
                                      http_proxy_auth=(unquote(proxy.username), unquote(proxy.password or "")) if proxy.username is not None else None)
                    else: kwargs.update(http_no_proxy=["*"])
                    raw = self.original.create_connection(url, **kwargs)
                    with transport.ws_lock: transport.ws = raw
                    transport.check()
                    class Socket:
                        def recv(self):
                            transport.check()
                            result = raw.recv()
                            transport.check()
                            return result
                        def send(self, value): transport.check(); return raw.send(value)
                        def settimeout(self, value): return raw.settimeout(min(value, transport.check()))
                        def close(self): return raw.close()
                    return Socket()
            for key, value in (("requests", Http(self.originals["requests"])), ("websocket", Sockets(self.originals["websocket"])), ("time", Clock(self.originals["time"]))):
                setattr(self.module, key, value)
            self.token = _ACTIVE.set(self)
            self.check()
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def abort(self):
        with self.ws_lock:
            if self.ws is not None:
                try: self.ws.abort()
                except Exception: pass

    def __exit__(self, *_):
        try:
            with self.ws_lock:
                if self.ws is not None:
                    try: self.ws.close()
                    except Exception: pass
                    self.ws = None
        finally:
            if self.token is not None: _ACTIVE.reset(self.token); self.token = None
            for key, value in self.originals.items(): setattr(self.module, key, value)
            self.originals.clear()
            _PATCH_LOCK.release()


class QQBinding:
    def __init__(self, service):
        self.service, self.root = service, service.data
        self.lock = threading.RLock()
        self.thread, self.transport = None, None
        self.cancelled = threading.Event()
        self.job, self.deadline = None, 0.

    @property
    def busy(self): return self.thread is not None and self.thread.is_alive()

    def status(self, job_id):
        with self.lock:
            if self.job is None or job_id != self.job["job_id"]: raise ValueError("绑定任务编号不匹配，请查询原任务。")
            return dict(self.job, busy=self.busy, remaining_seconds=max(0, int(self.deadline - time.monotonic())) if self.busy else 0)

    def start(self, is_group, revision):
        if type(is_group) is not bool: raise ValueError("请选择单聊或群聊绑定。")
        self.service._assert_idle()
        lease = profile_lock(self.root)
        lease.acquire()
        try:
            from .onedragon_environment import EnvironmentStore, validate_url
            from .onedragon_notify import NotificationStore
            store = NotificationStore(self.service)
            push, _ = store._channel("QQ_BOT")
            if config_revision(push.push_config) != revision: raise ValueError("渠道设置已变化，请重新读取后绑定。")
            values = push.get_channel_config("QQ_BOT")
            if any(not isinstance(values[k], str) or not values[k].strip() for k in ("APP_ID", "CLIENT_SECRET")):
                raise ValueError("请先保存机器人 AppID 和 AppSecret。")
            env = EnvironmentStore(self.service).config("_environment")
            self.service.ctx.__dict__["env_config"] = env
            proxy = ""
            if push.push_config.proxy not in ("NONE", "PERSONAL"): raise ValueError("发送代理配置无效。")
            if push.push_config.proxy == "PERSONAL":
                if not env.is_personal_proxy or not env.personal_proxy: raise ValueError("请先配置个人代理。")
                proxy = env.personal_proxy
                validate_url(proxy, proxy=True)
                if urlsplit(proxy).scheme == "https": raise ValueError("QQ 网关绑定不支持 HTTPS 代理，请使用 HTTP 或 SOCKS 代理。")
                if urlsplit(proxy).scheme.startswith("socks") and any(importlib.util.find_spec(name) is None for name in ("socks", "python_socks")):
                    raise ValueError("当前一条龙环境缺少 SOCKS 依赖，请使用 HTTP 代理。")
            push._refresh_secrets()
            with self.lock:
                self.cancelled = threading.Event()
                self.deadline = time.monotonic() + 60
                self.job = dict(job_id=uuid.uuid4().hex, target="group" if is_group else "user", state="connecting", code="", detail="正在连接 QQ 网关…", saved=False,
                                started_at=datetime.now().astimezone().isoformat())
                self.thread = threading.Thread(target=self._run, args=(lease, push, values, revision, proxy, is_group), name="AutoZZZ-QQ-Bind", daemon=False)
                try: self.thread.start()
                except BaseException:
                    self.thread = None
                    self.job.update(state="failed", detail="无法创建 QQ 绑定线程。")
                    raise
                return self.status(self.job["job_id"])
        except BaseException:
            lease.close()
            raise

    def _update(self, **values):
        with self.lock:
            if not self.cancelled.is_set(): self.job.update(values)

    def _run(self, lease, push, values, revision, proxy, is_group):
        try:
            from one_dragon.base.push.channel import qq_bot
            transport = GatewayTransport(qq_bot, self.cancelled, self.deadline, proxy, push.protect_transient)
            with self.lock: self.transport = transport
            def code_ready(code):
                if not isinstance(code, str) or len(code) != 4 or not code.isascii() or not code.isdigit(): raise ValueError("QQ 返回的匹配码无效。")
                self._update(state="waiting", code=code, detail="请在群里 @机器人 发送匹配码。" if is_group else "请私聊机器人发送匹配码。")
            with transport:
                if is_group:
                    recipient, error = qq_bot.bind_group_openid(values["APP_ID"].strip(), values["CLIENT_SECRET"].strip(), code_ready,
                        lambda text: self._update(detail=redact(text, push.secrets)), self.cancelled)
                else:
                    recipient, error = qq_bot.bind_user_openid(values["APP_ID"].strip(), values["CLIENT_SECRET"].strip(), code_ready, self.cancelled)
                push.protect_transient(recipient)
                transport.check()
            with self.lock:
                transport.check()
                if self.cancelled.is_set(): raise BindingCancelled("已取消绑定。")
                if not recipient: raise RuntimeError(error or "未获取到收件方，请重新绑定。")
                if not isinstance(recipient, str) or len(recipient) > 4096 or any(ord(c) < 32 for c in recipient): raise ValueError("QQ 收件方格式无效。")
                push.reload_config()
                config = push.push_config
                atomic_apply(config, revision, lambda: config.update_channel_config_value("QQ_BOT", "GROUP_OPENID" if is_group else "OPENID", recipient), self.root)
                from one_dragon.base.config.yaml_operator import invalidate_cache
                invalidate_cache(str(config.file_path))
                push.reload_config()
                self.job.update(state="success", code="", detail="群聊收件方已保存。" if is_group else "单聊收件方已保存。", saved=True)
        except Exception as exc:
            with self.lock:
                state = "cancelled" if self.cancelled.is_set() or isinstance(exc, BindingCancelled) else "timeout" if isinstance(exc, TimeoutError) else "failed"
                self.job.update(state=state, code="", detail="已取消绑定。" if state == "cancelled" else redact(str(exc), push.secrets), saved=False)
        finally:
            try: lease.close()
            except Exception as exc:
                with self.lock:
                    self.job.update(state="failed", detail=("收件方已保存；" if self.job["saved"] else "") + "配置锁清理失败：" + redact(str(exc), push.secrets))
            finally:
                with self.lock:
                    self.transport = None
                    self.job["finished_at"] = datetime.now().astimezone().isoformat()

    def cancel(self, job_id):
        with self.lock:
            self.status(job_id)
            if self.busy and self.job["state"] in ("connecting", "waiting", "cancelling"):
                self.cancelled.set()
                self.job.update(state="cancelling", code="", detail="正在取消，等待原连接退出…")
                transport = self.transport
            else: transport = None
        if transport is not None: transport.abort()
        return self.status(job_id)

    def close(self):
        if self.job is not None: self.cancel(self.job["job_id"])
        if self.thread is not None: self.thread.join()
