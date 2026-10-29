"""原生通知配置：沿用上游领域配置，读取时不返回渠道凭据。"""
from __future__ import annotations

import json
import re
from urllib.parse import parse_qsl, unquote, urlsplit
from typing import Any

from .config_transaction import atomic_apply, config_revision
from .run_lock import profile_lock

LIFECYCLE = [("关闭", "off"), ("仅结束", "finish_only"), ("开始和结束", "start_and_finish")]
DETAIL = [("关闭", "off"), ("仅失败", "error_only"), ("逐条", "all"), ("合并", "merge")]


def credential_fragments(values):
    result = set()
    def add(value):
        if isinstance(value, dict):
            for item in value.values(): add(item)
        elif isinstance(value, list):
            for item in value: add(item)
        elif isinstance(value, str) and value and not value.startswith(("$", "{{")):
            result.add(value)
            if value.lower().startswith(("bearer ", "basic ")): result.add(value.split(" ", 1)[1])
    for value in values:
        if not value: continue
        add(value)
        try: add(json.loads(value))
        except (TypeError, ValueError): pass
        for line in value.splitlines():
            if ":" in line and not line.lstrip().startswith(("http:", "https:")): add(line.split(":", 1)[1].strip())
        if "://" in value:
            try:
                url = urlsplit(value)
                add(url.hostname)
                for fragment in url.path.split("/"):
                    if len(fragment) >= 4: add(unquote(fragment))
                for _, fragment in parse_qsl(url.query): add(fragment)
            except ValueError: pass
    return tuple(result)


def redact(text: Any, secrets=(), limit=2000) -> str:
    value = str(text)
    for secret in sorted({str(s) for s in secrets if s}, key=len, reverse=True):
        value = value.replace(secret, "[已隐藏]")
    value = re.sub(r"(?i)\b(?:https?|socks5h?)://[^\s\]\)>\"']+", "[地址已隐藏]", value)
    return value if limit is None else value[:limit]


def validate_headers(text: str) -> None:
    if not text.strip(): return
    if text.lstrip().startswith("{"):
        try: values = json.loads(text)
        except ValueError: raise ValueError("请求头须为 JSON 对象，或每行一个名称: 值。") from None
        if not isinstance(values, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in values.items()):
            raise ValueError("请求头的名称和值均须为文本。")
    elif any(":" not in line or not line.split(":", 1)[0].strip() for line in text.splitlines() if line.strip()):
        raise ValueError("请求头每行须为名称: 值。")


def validate_modes(rows: Any, app_map: dict) -> None:
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("通知时机须为应用记录列表。")
    if any(not isinstance(row.get("app_id"), str) for row in rows) or len(rows) != len(app_map) or {row.get("app_id") for row in rows} != set(app_map):
        raise ValueError("通知时机须包含每个应用且不能重复。")
    for row in rows:
        if (set(row) != {"app_id", "lifecycle", "detail"}
            or not isinstance(row["lifecycle"], str) or not isinstance(row["detail"], str)
            or row["lifecycle"] not in {v for _, v in LIFECYCLE}
            or row["detail"] not in {v for _, v in DETAIL}):
            raise ValueError("通知时机包含未知模式或字段。")


class NotificationStore:
    def __init__(self, service):
        self.service, self.ctx, self.root = service, service.ctx, service.data

    def config(self):
        from one_dragon.base.config.yaml_operator import cached_yaml_data
        cached_yaml_data.clear()
        self.ctx.__dict__.pop("notify_config", None)
        return self.ctx.notify_config

    def describe(self):
        self.service._assert_idle()
        with profile_lock(self.root):
            config = self.config()
            modes = []
            for app_id, name in config.app_map.items():
                lifecycle, detail = config.get_app_notify_modes(app_id)
                modes.append(dict(app_id=app_id, lifecycle=lifecycle, detail=detail))
            return dict(app_id="notify", revision=config_revision(config), fields=[
                dict(name="enable_notify", label="启用自动通知", kind="bool", value=config.enable_notify, options=[]),
                dict(name="title", label="通知标题", kind="str", value=config.title, options=[]),
                dict(name="merge_error_immediate_notify", label="合并模式下立即发送失败通知", kind="bool",
                     value=config.merge_error_immediate_notify, options=[]),
                dict(name="notification_modes", label="应用通知时机", kind="notification_modes", value=modes,
                     options=[dict(label=name, value=key) for key, name in config.app_map.items()]),
            ])

    def save(self, values, revision):
        self.service._assert_idle()
        allowed = {"enable_notify", "title", "merge_error_immediate_notify", "notification_modes"}
        if not isinstance(values, dict) or set(values) - allowed:
            raise ValueError("通知设置包含未知字段。")
        with profile_lock(self.root):
            config = self.config()
            for key in ("enable_notify", "merge_error_immediate_notify"):
                if key in values and type(values[key]) is not bool: raise ValueError("通知开关类型无效。")
            if "title" in values and (not isinstance(values["title"], str) or not values["title"].strip()
                                      or len(values["title"]) > 200 or any(c in values["title"] for c in "\r\n\0")):
                raise ValueError("通知标题须为 1—200 字的单行文本。")
            if "notification_modes" in values: validate_modes(values["notification_modes"], config.app_map)
            if values.get("enable_notify"):
                if not self.ctx.push_service.available_channels():
                    raise ValueError("请先保存并启用至少一个完整的发送渠道。")
            def apply():
                for key, value in values.items():
                    if key == "notification_modes":
                        for row in value: config.set_app_notify_modes(row["app_id"], row["lifecycle"], row["detail"])
                    else: setattr(config, key, value)
            atomic_apply(config, revision, apply, self.root)
            from one_dragon.base.config.yaml_operator import invalidate_cache
            invalidate_cache(str(config.file_path))
        return self.describe()

    def channels(self):
        self.service._assert_idle()
        with profile_lock(self.root):
            push = self.ctx.push_service
            push.reload_config()
            return [dict(id=c.channel_id, name=c.channel_name) for c in push.channels if c.channel_id != "FAKE"]

    def _channel(self, channel_id):
        push = self.ctx.push_service
        push.reload_config()
        if channel_id == "_settings": return push, None
        channel = push._id_2_channels.get(channel_id)
        if channel is None or channel_id == "FAKE": raise ValueError("发送渠道不存在。")
        return push, channel

    def describe_channel(self, channel_id):
        self.service._assert_idle()
        with profile_lock(self.root):
            push, channel = self._channel(channel_id)
            config = push.push_config
            if channel is None:
                return dict(id=channel_id, name="发送设置", revision=config_revision(config), fields=[
                    dict(name="send_image", label="发送游戏截图（可能包含账号标识）", kind="bool", value=config.send_image, options=[]),
                    dict(name="proxy", label="发送代理", kind="enum", value=config.proxy,
                         options=[dict(label="不使用代理", value="NONE"), dict(label="个人代理", value="PERSONAL")])])
            values = push.get_channel_config(channel_id)
            valid, error = push.validate_channel(channel)
            fields = []
            for field in channel.config_schema:
                combo = field.field_type.value == "combo"
                stored = config.get_channel_config_key(channel_id, field.var_suffix) in config.data
                fields.append(dict(name=field.var_suffix, label=field.title + (" *" if field.required else ""),
                    kind="enum" if combo else "private_text" if field.field_type.value in ("key_value", "code_editor") else "secret",
                    value=values[field.var_suffix] if combo else "" if stored else field.default,
                    options=[dict(label=v, value=v) for v in field.options], configured=bool(values[field.var_suffix]),
                    format="每行：名称: 值，或 JSON 对象" if field.field_type.value == "key_value" else
                        "$title · $content · $image" if field.field_type.value == "code_editor" else "",
                    language=field.language))
            return dict(id=channel_id, name=channel.channel_name, revision=config_revision(config),
                        enabled=channel_id in push.enabled_channels(), valid=valid, error=error, fields=fields,
                        binding_supported=False if channel_id == "QQ_BOT" else None)

    def save_channel(self, channel_id, values, revision):
        self.service._assert_idle()
        if not isinstance(values, dict): raise ValueError("渠道设置须为字段记录。")
        with profile_lock(self.root):
            push, channel = self._channel(channel_id)
            config = push.push_config
            if channel is None:
                if set(values) - {"send_image", "proxy"}: raise ValueError("发送设置包含未知字段。")
                if "send_image" in values and type(values["send_image"]) is not bool: raise ValueError("截图开关类型无效。")
                if "proxy" in values and values["proxy"] not in ("NONE", "PERSONAL"): raise ValueError("发送代理无效。")
                if values.get("proxy") == "PERSONAL" and not self.ctx.env_config.is_personal_proxy:
                    raise ValueError("一条龙环境尚未配置个人代理。")
                atomic_apply(config, revision, lambda: [setattr(config, k, v) for k, v in values.items()], self.root)
            else:
                schema = {field.var_suffix: field for field in channel.config_schema}
                allowed = set(schema) | {"clear_" + k for k in schema} | {"enabled"}
                if set(values) - allowed: raise ValueError("渠道设置包含未知字段。")
                current = push.get_channel_config(channel_id)
                updates = {}
                for key, field in schema.items():
                    clear = values.get("clear_" + key, False)
                    if type(clear) is not bool: raise ValueError("清除开关类型无效。")
                    text = values.get(key, "")
                    if not isinstance(text, str) or len(text) > 65536 or "\0" in text: raise ValueError(field.title + "：文本过长或无效。")
                    if clear and text: raise ValueError(field.title + "：不能同时输入和清除。")
                    if field.field_type.value == "combo" and key in values:
                        if text not in field.options: raise ValueError(field.title + "：请选择有效值。")
                        updates[key] = text
                    elif clear: updates[key] = ""
                    elif text: updates[key] = text
                candidate = dict(current, **updates)
                enabled = values.get("enabled", channel_id in push.enabled_channels())
                if type(enabled) is not bool: raise ValueError("渠道启用开关无效。")
                if channel_id == "WEBHOOK" and (enabled or "HEADERS" in updates): validate_headers(candidate.get("HEADERS", ""))
                if enabled:
                    for key, field in schema.items():
                        if field.required and not candidate[key].strip(): raise ValueError(field.title + "不能为空。")
                    ok, error = push.validate_channel(channel, candidate)
                    if not ok: raise ValueError(error)
                def apply():
                    for key, value in updates.items(): config.update_channel_config_value(channel_id, key, value)
                    channels = set(push.enabled_channels())
                    if enabled: channels.add(channel_id)
                    else: channels.discard(channel_id)
                    config.update("autozzz_enabled_channels", sorted(channels))
                atomic_apply(config, revision, apply, self.root)
            from one_dragon.base.config.yaml_operator import invalidate_cache
            invalidate_cache(str(config.file_path))
            push.reload_config()
        return self.describe_channel(channel_id)

    def test(self, channel_id, revision):
        self.service._assert_idle()
        with profile_lock(self.root):
            push, channel = self._channel(channel_id)
            if channel is None: raise ValueError("请选择具体发送渠道。")
            if config_revision(push.push_config) != revision: raise ValueError("渠道设置已变化，请重新读取。")
            ok, error = push.validate_channel(channel)
            if not ok: raise ValueError(error)
            ok, detail = push._deliver("AutoZZZ 通知测试", "这是一条来自 AutoZZZ 的测试通知。", channel_id=channel_id, manual=True)
            return dict(push.status(), test_result=dict(channel=channel_id, success=ok, detail=detail))
