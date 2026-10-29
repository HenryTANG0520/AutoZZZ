"""保留实际上游传输器，补足启用边界、逐渠道结果和日志脱敏。"""
from __future__ import annotations

from datetime import datetime
from functools import cached_property, wraps
import copy
import logging
import threading
import traceback

from one_dragon.base.push.push_service import PushService
from one_dragon.base.operation.notify_pool import NotifyPoolItem
from one_dragon.utils.log_utils import log

from .onedragon_notify import credential_fragments, redact


class _RedactFilter(logging.Filter):
    def __init__(self, service):
        super().__init__()
        self.service = service

    def filter(self, record):
        record.msg = redact(record.getMessage(), self.service.secrets, limit=None)
        record.args = ()
        if record.exc_info:
            record.exc_text = redact("".join(traceback.format_exception(*record.exc_info)), self.service.secrets, limit=None)
            record.exc_info = None
        if record.stack_info: record.stack_info = redact(record.stack_info, self.service.secrets, limit=None)
        return True


class ManagedPushService(PushService):
    def __init__(self, ctx):
        super().__init__(ctx)
        self.result_lock = threading.RLock()
        self.results = []
        self.pending = 0
        self.secrets = ()
        self.log_filter = _RedactFilter(self)
        log.addFilter(self.log_filter)

    @cached_property
    def push_config(self):
        config = PushService.push_config.func(self)
        if "send_image" not in config.data: config.send_image = False
        return config

    def reload_config(self):
        from one_dragon.base.config.yaml_operator import cached_yaml_data
        cached_yaml_data.clear()
        self.__dict__.pop("push_config", None)
        self.push_config
        self._refresh_secrets()

    def _refresh_secrets(self):
        values = []
        for channel in self.channels:
            config = self.get_channel_config(channel.channel_id)
            values.extend(config[f.var_suffix] for f in channel.config_schema if f.field_type.value != "combo" and config[f.var_suffix])
        if self.ctx.env_config.is_personal_proxy: values.append(self.ctx.env_config.personal_proxy)
        self.secrets = credential_fragments(values)

    def enabled_channels(self):
        channels = self.push_config.get("autozzz_enabled_channels", [])
        if not isinstance(channels, list) or any(not isinstance(value, str) for value in channels):
            raise ValueError("通知渠道启用记录无效。")
        return set(channels)

    def validate_channel(self, channel, config=None):
        self._refresh_secrets()
        try:
            values = self.get_channel_config(channel.channel_id) if config is None else config
            ok, error = channel.validate_config(values)
            return bool(ok), redact(error, (*self.secrets, *credential_fragments(values.values())))
        except Exception as exc:
            return False, "渠道配置校验失败：" + type(exc).__name__

    def available_channels(self):
        self.push_config
        enabled = self.enabled_channels()
        return [c.channel_id for c in self.channels if c.channel_id in enabled and c.channel_id != "FAKE" and self.validate_channel(c)[0]]

    def _record(self, channel, success, detail, kind):
        item = dict(channel=channel, success=bool(success), detail=redact(detail, self.secrets), kind=kind,
                    time=datetime.now().astimezone().isoformat())
        with self.result_lock:
            self.results.append(item)
        log.info("通知 %s %s：%s", channel, "已发送" if success else "失败", item["detail"])

    def _deliver(self, title, content=None, image=None, channel_id=None, *, items=None, manual=False):
        self.push_config
        self._refresh_secrets()
        if not manual:
            enabled_setting = self.ctx.notify_config.enable_notify
            if type(enabled_setting) is not bool or type(self.push_config.send_image) is not bool:
                self._record("设置", False, "通知或截图开关类型无效。", "message")
                return False, "通知或截图开关类型无效。"
            if not enabled_setting: return False, "通知已关闭"
        enabled = self.enabled_channels()
        selected = [channel_id] if channel_id else [c.channel_id for c in self.channels if c.channel_id in enabled and c.channel_id != "FAKE"]
        kind = "merged" if items is not None else "message"
        if not selected:
            self._record("配置", False, "没有已启用的发送渠道。", kind)
            return False, "没有已启用的发送渠道。"
        errors = []
        for cid in selected:
            channel = self._id_2_channels.get(cid)
            if channel is None or cid == "FAKE" or (not manual and cid not in enabled):
                ok, detail = False, "渠道未启用或不存在。"
            else:
                ok, detail = self.validate_channel(channel)
                if ok:
                    try:
                        config = self.get_channel_config(cid)
                        if items is None:
                            ok, detail = channel.push(config=config, title=title, content=content,
                                image=image if self.push_config.send_image is True and not manual else None, proxy_url=self.get_proxy())
                        else:
                            payload = list(items) if self.push_config.send_image is True else [NotifyPoolItem(content=item.content) for item in items]
                            ok, detail = channel.push_merged(config=config, title=title, items=payload, proxy_url=self.get_proxy())
                    except Exception as exc:
                        ok, detail = False, type(exc).__name__ + ": " + str(exc)
            if type(ok) is not bool: ok, detail = False, "渠道返回了无效的发送结果。"
            self._record(cid, ok, detail, kind)
            if not ok: errors.append(cid + "：" + redact(detail, self.secrets))
        return not errors, "；".join(errors)

    def push(self, title, content, image=None, channel_id=None):
        return self._deliver(title, content, image, channel_id)

    def push_merged(self, title, items, channel_id=None):
        return self._deliver(title, channel_id=channel_id, items=list(items))

    def _queue(self, function, *args):
        with self.result_lock: self.pending += 1
        def completed(future):
            try:
                future.result()
            except Exception as exc:
                self._record("发送服务", False, "发送异常：" + type(exc).__name__, "message")
            finally:
                with self.result_lock: self.pending -= 1
        try:
            future = self._executor.submit(function, *args)
        except Exception:
            with self.result_lock: self.pending -= 1
            self._record("发送服务", False, "发送队列不可用。", "message")
        else: future.add_done_callback(completed)

    def push_async(self, title, content, image=None, channel_id=None):
        self._queue(self.push, title, content, image, channel_id)

    def push_merged_async(self, title, items, channel_id=None):
        self._queue(self.push_merged, title, list(items), channel_id)

    def flush(self):
        self._executor.submit(lambda: None).result()

    def reset_results(self):
        self.flush()
        with self.result_lock: self.results.clear()

    def status(self):
        with self.result_lock: results, pending = [dict(item) for item in self.results], self.pending
        return dict(enabled=self.ctx.notify_config.enable_notify is True, sent=sum(item["success"] for item in results),
                    failed=sum(not item["success"] for item in results), pending=pending, attempts=results[-200:])

    def after_app_shutdown(self):
        try: super().after_app_shutdown()
        finally: log.removeFilter(self.log_filter)


class NotificationControl:
    def __init__(self, ctx):
        self.ctx, self.original, self.init_original = ctx, None, None

    def install(self):
        from zzz_od.application.notify.notify_app import NotifyApp
        original = NotifyApp.notify
        @wraps(original)
        def notify(operation):
            if operation.ctx is not self.ctx: return original(operation)
            if not self.ctx.notify_config.enable_notify: return operation.round_success("通知已关闭")
            result = getattr(operation, "_autozzz_notification", None)
            if result is None:
                # 先记尝试，再进入外部传输；失焦重观察不能重复发送未知结果。
                result = operation._autozzz_notification = dict(success=False, detail="发送结果未知。")
                operation.exist_failure = False
                try:
                    message = operation.format_message().replace("一条龙运行完成：", "AutoZZZ 运行完成：", 1).replace("全部失败❌", "没有可汇总的近期运行记录。", 1)
                    ok, detail = self.ctx.push_service.push(self.ctx.notify_config.title, message, operation.last_screenshot)
                    result.update(success=ok and not operation.exist_failure,
                                  detail=detail or ("已发送；部分应用失败。" if operation.exist_failure else "通知已发送"))
                except Exception as exc:
                    result["detail"] = "发送失败：" + type(exc).__name__
            return operation.round_success(result["detail"]) if result["success"] else operation.round_fail(result["detail"])
        annotation = copy.copy(original.operation_node_annotation)
        annotation.op_method = notify
        notify.operation_node_annotation = annotation
        self.original = original
        NotifyApp.notify = notify
        self.init_local = "handle_init" in NotifyApp.__dict__
        self.init_original = NotifyApp.handle_init
        original_init = self.init_original
        @wraps(original_init)
        def init(operation):
            if operation.ctx is self.ctx: operation._autozzz_notification = None
            return original_init(operation)
        NotifyApp.handle_init = init

    def close(self):
        if self.original is not None:
            from zzz_od.application.notify.notify_app import NotifyApp
            NotifyApp.notify, self.original = self.original, None
            if self.init_local: NotifyApp.handle_init = self.init_original
            else: delattr(NotifyApp, "handle_init")
            self.init_original = None
