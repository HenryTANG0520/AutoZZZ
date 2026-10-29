"""实际 SDK 的环境与识别模型配置；地址只写入，不在描述中回传。"""
from __future__ import annotations

from urllib.parse import urlsplit

from .config_transaction import atomic_apply, config_revision
from .run_lock import profile_lock

PROXY_SCHEMES = {"http", "https", "socks4", "socks4a", "socks5", "socks5h"}


def field_value(name, label, value, kind="str", options=None, **metadata):
    return dict(name=name, label=label, value=value, kind=kind, options=options or [], **metadata)


def validate_url(value, *, proxy=False):
    if not isinstance(value, str) or len(value) > 4096 or any(c.isspace() or ord(c) < 32 for c in value):
        raise ValueError("地址不能包含空白或控制字符。")
    try:
        parts = urlsplit(value)
        allowed = PROXY_SCHEMES if proxy else {"http", "https"}
        if parts.scheme not in allowed or not parts.hostname or parts.fragment or parts.query or parts.port == 0:
            raise ValueError
        if any(c in parts.hostname for c in '\\/@<>"'): raise ValueError
        if proxy and parts.path not in ("", "/"): raise ValueError
        if not proxy and (parts.username is not None or parts.password is not None): raise ValueError
        if parts.netloc.endswith(":"): raise ValueError
    except ValueError:
        raise ValueError("代理格式为 协议://主机:端口。" if proxy else "GitHub 代理须为完整 HTTP(S) 地址。") from None


def validate_environment(config, repo):
    from one_dragon.envs.env_config import ProxyTypeEnum, ScreenshotMethodEnum
    if config.proxy_type not in {item.value.value for item in ProxyTypeEnum}:
        raise ValueError("代理类型无效。")
    for key in ("auto_fetch_gh_proxy_url", "is_debug"):
        if type(getattr(config, key)) is not bool: raise ValueError("环境开关类型无效。")
    if config.personal_proxy: validate_url(config.personal_proxy, proxy=True)
    if config.gh_proxy_url: validate_url(config.gh_proxy_url)
    if config.is_personal_proxy and not config.personal_proxy: raise ValueError("请输入个人代理地址。")
    if config.is_gh_proxy and not config.gh_proxy_url: raise ValueError("请输入 GitHub 代理地址。")
    if config.screenshot_method not in {item.value.value for item in ScreenshotMethodEnum}:
        raise ValueError("截图方式无效。")
    if config.resource_source not in {item.value for item in repo.resource_source_options}:
        raise ValueError("资源下载源无效。")


class EnvironmentStore:
    def __init__(self, service):
        self.service, self.ctx, self.root = service, service.ctx, service.data

    def config(self, app_id):
        from one_dragon.base.config.yaml_operator import cached_yaml_data
        cached_yaml_data.clear()
        if app_id == "_environment":
            from one_dragon.envs.env_config import EnvConfig
            return EnvConfig(self.ctx.repo_config)
        if app_id == "_models":
            from zzz_od.config.model_config import ModelConfig
            return ModelConfig(self.ctx.repo_config)
        raise ValueError("环境配置不存在。")

    def _ocr_options(self):
        from one_dragon.base.matcher.ocr.onnx_ocr_matcher import DEFAULT_OCR_MODEL_NAME, DEPRECATED_OCR_MODEL_NAMES
        names = {DEFAULT_OCR_MODEL_NAME}
        folder = self.root / "assets" / "models" / "onnx_ocr"
        if folder.is_dir():
            for path in folder.iterdir():
                if (path.is_dir() and not path.is_symlink() and path.resolve().parent == folder.resolve()
                    and path.name not in DEPRECATED_OCR_MODEL_NAMES
                    and all((path / name).is_file() for name in ("det.onnx", "rec.onnx", "cls.onnx", "simfang.ttf"))
                    and any(path.glob("*_dict.txt"))):
                    names.add(path.name)
        return [dict(label=name + (" · 推荐" if name == DEFAULT_OCR_MODEL_NAME else " · 本地"), value=name) for name in sorted(names)]

    def _describe(self, app_id, config):
        fields = []
        if app_id == "_environment":
            from one_dragon.envs.env_config import ProxyTypeEnum, ScreenshotMethodEnum
            def enum(name, label, choices, group):
                fields.append(field_value(name, label, getattr(config, name), "enum", choices, group=group))
            enum("proxy_type", "代理", [dict(label=v.value.label, value=v.value.value) for v in ProxyTypeEnum], "网络")
            for key, label in (("personal_proxy", "个人代理地址"), ("gh_proxy_url", "GitHub 代理地址")):
                configured = bool(getattr(config, key))
                fields.append(field_value(key, label, "", "secret", group="网络", configured=configured,
                    hint=("已配置 · 留空保留。" if configured else "") +
                        ("格式：协议://主机:端口" if key == "personal_proxy" else "格式：https://代理地址")))
                fields.append(field_value("clear_" + key, "清除" + label, False, "bool", group="网络"))
            fields.append(field_value("auto_fetch_gh_proxy_url", "自动获取 GitHub 代理地址", config.auto_fetch_gh_proxy_url, "bool", group="网络"))
            enum("resource_source", "资源下载源", [dict(label=v.label, value=v.value) for v in config.repo_config.resource_source_options], "下载")
            enum("screenshot_method", "截图方式", [dict(label=v.value.label, value=v.value.value) for v in ScreenshotMethodEnum], "运行")
            fields.append(field_value("is_debug", "详细日志", config.is_debug, "bool", group="运行"))
        else:
            from one_dragon.base.matcher.ocr.onnx_ocr_matcher import DEFAULT_OCR_MODEL_NAME
            fields.extend([
                field_value("ocr", "文字识别模型", config.get("ocr", DEFAULT_OCR_MODEL_NAME), "enum", self._ocr_options(), group="文字识别"),
                field_value("ocr_use_gpu", "优先使用 GPU", config.ocr_use_gpu, "bool", group="文字识别"),
            ])
            for resource in config.get_model_resources():
                backup = getattr(config, resource.config_key + "_backup")
                choices = [dict(label=resource.default_model + " · 推荐", value=resource.default_model), dict(label=backup + " · 备用", value=backup)]
                fields.append(field_value(resource.config_key, "模型版本", config.get(resource.config_key, resource.default_model), "enum", choices, group=resource.display_name))
                if resource.gpu_config_key:
                    fields.append(field_value(resource.gpu_config_key, "优先使用 GPU", getattr(config, resource.gpu_config_key), "bool", group=resource.display_name))
        return dict(app_id=app_id, revision=config_revision(config), fields=fields)

    def describe(self, app_id):
        self.service._assert_idle()
        with profile_lock(self.root): return self._describe(app_id, self.config(app_id))

    def validate_models(self, config=None):
        config = config or self.config("_models")
        for field in self._describe("_models", config)["fields"]:
            value = field["value"]
            if field["kind"] == "bool" and type(value) is not bool:
                raise ValueError(field["group"] + "：GPU 开关类型无效。")
            if field["kind"] == "enum" and value not in {o["value"] for o in field["options"]}:
                raise ValueError(field["group"] + "：模型版本无效，请重新选择。")

    def save(self, app_id, values, revision):
        self.service._assert_idle()
        with profile_lock(self.root):
            config = self.config(app_id)
            fields = {f["name"]: f for f in self._describe(app_id, config)["fields"]}
            if not isinstance(values, dict) or set(values) - set(fields): raise ValueError("环境设置包含未知字段。")
            updates = {}
            for key, value in values.items():
                field = fields[key]
                if field["kind"] == "bool" and type(value) is not bool: raise ValueError(field["label"] + "须为开关值。")
                if field["kind"] == "enum" and not any(type(value) is type(o["value"]) and value == o["value"] for o in field["options"]):
                    raise ValueError(field["label"] + "：请选择有效值。")
                if field["kind"] == "secret":
                    if not isinstance(value, str): raise ValueError(field["label"] + "须为文本。")
                    if value and values.get("clear_" + key): raise ValueError(field["label"] + "不能同时输入和清除。")
                    if value: validate_url(value, proxy=key == "personal_proxy")
                    elif not values.get("clear_" + key): continue
                if key.startswith("clear_"):
                    if value: updates[key.removeprefix("clear_")] = ""
                else: updates[key] = value
            # 校验候选值时只改内存；所有领域 setter 最终统一原子发布。
            if app_id == "_environment":
                from types import SimpleNamespace
                names = ("proxy_type", "personal_proxy", "gh_proxy_url", "auto_fetch_gh_proxy_url", "resource_source", "screenshot_method", "is_debug")
                candidate = {name: updates.get(name, getattr(config, name)) for name in names}
                candidate.update(is_personal_proxy=candidate["proxy_type"] == "personal", is_gh_proxy=candidate["proxy_type"] == "ghproxy")
                validate_environment(SimpleNamespace(**candidate), self.ctx.repo_config)
            else:
                candidate = self.config("_models")
                for key, value in updates.items(): candidate.data[key] = value
                self.validate_models(candidate)
            atomic_apply(config, revision, lambda: [setattr(config, k, v) for k, v in updates.items()], self.root)
            from one_dragon.base.config.yaml_operator import invalidate_cache
            invalidate_cache(str(config.file_path))
            # 提供当前配置给同连接里的通知编辑器；运行必须重新准备完整新进程。
            self.ctx.__dict__["env_config" if app_id == "_environment" else "model_config"] = config
            self.ctx.push_service._refresh_secrets()
            if self.service._execution is not None:
                self.service._environment_changed = True
        return self.describe(app_id)
