"""先在内存调用配置领域方法，再原子发布一个配置文件。"""
from __future__ import annotations

import copy
import hashlib
import os
from pathlib import Path
from typing import Any, Callable
import uuid


def config_revision(config: Any) -> str:
    target = Path(config._get_write_path()).resolve()
    raw = target.read_bytes() if target.exists() else b""
    return hashlib.sha256(str(target).casefold().encode("utf-8") + b"\0" + raw).hexdigest()


def atomic_apply(config: Any, expected_revision: str, apply: Callable[[], None], root: Path, guard: Callable[[], bool] | None = None) -> None:
    target = Path(config._get_write_path()).resolve()
    if not target.is_relative_to(Path(root).resolve()) or target.suffix != ".yml":
        raise ValueError("配置路径必须位于本实例数据目录。")
    if config_revision(config) != expected_revision:
        raise ValueError("配置已被其他窗口或任务修改，请重新读取后保存。")
    snapshot = copy.deepcopy(config.__dict__)
    original_save = config.save
    temporary = target.with_name(target.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        config.save = lambda: None
        apply()
        config.__dict__.pop("save", None)
        if "save" in snapshot:
            config.__dict__["save"] = snapshot["save"]
        target.parent.mkdir(parents=True, exist_ok=True)
        # 原 save 会同步 plan_list 等专用成员；仅将其最终写入重定向到临时文件。
        config._get_write_path = lambda: str(temporary)
        original_save()
        if not temporary.is_file():
            raise OSError("上游配置没有生成可发布文件。")
        if config_revision_from_path(target) != expected_revision:
            raise ValueError("保存期间配置发生变化。")
        if guard is not None and not guard(): raise ValueError("保存期间来源发生变化，请重新读取。")
        os.replace(temporary, target)
        config.file_path = str(target)
        config._write_file_path = str(target)
        if hasattr(config, "old_file_path"):
            config.old_file_path = str(target)
        if hasattr(config, "_copy_on_write_source_path"):
            config._copy_on_write_source_path = None
        config.__dict__.pop("_get_write_path", None)
        if "_get_write_path" in snapshot:
            config.__dict__["_get_write_path"] = snapshot["_get_write_path"]
    except Exception:
        config.__dict__.clear()
        config.__dict__.update(snapshot)
        raise
    finally:
        temporary.unlink(missing_ok=True)


def config_revision_from_path(target: Path) -> str:
    raw = target.read_bytes() if target.exists() else b""
    return hashlib.sha256(str(target).casefold().encode("utf-8") + b"\0" + raw).hexdigest()
