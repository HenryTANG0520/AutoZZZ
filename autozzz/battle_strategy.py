"""Versioned user-editable combat data, with no executable code or spending settings."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path

BUILTINS = ("builtin:bad-women-global", "builtin:cn-team1")
STRATEGY_FIELDS = {"team", "expected_roster", "script", "hold_unit", "intro_wait", "cycles", "timeout"}


def load_strategy(reference: str, *, root: Path, resource_root: Path):
    if reference.startswith("builtin:"):
        if reference not in BUILTINS:
            raise ValueError("未知的内置战斗策略。")
        path = resource_root / "assets" / "strategies" / (reference.split(":", 1)[1] + ".json")
    else:
        path = Path(reference).expanduser()
        if not path.is_absolute():path = root / path
    raw = path.read_bytes()
    if len(raw)>32768:raise ValueError("策略文件不能超过32 KiB。")
    data = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(data,dict) or type(data.get("version")) is not int or data["version"]!=1:
        raise ValueError("策略必须是 version 为1的JSON对象。")
    unknown = set(data) - STRATEGY_FIELDS - {"version", "name", "description"}
    if unknown:raise ValueError("策略含不支持的字段："+", ".join(sorted(unknown)))
    if not isinstance(data.get("name"),str) or not data["name"].strip():
        raise ValueError("策略缺少名称 name。")
    if not isinstance(data.get("script"),str) or not data["script"].strip():
        raise ValueError("策略缺少预输入脚本 script。")
    values={key:data[key] for key in STRATEGY_FIELDS if key in data}
    return values,dict(name=data["name"],path=str(path.resolve()),sha256=hashlib.sha256(raw).hexdigest(),version=1)


def resolve_strategy(plan, *, root: Path, resource_root: Path):
    """Freeze data once per run; later disk changes only affect the next run."""
    plan.validate()
    if not plan.strategy_file:return replace(plan),None
    values,metadata=load_strategy(plan.strategy_file,root=root,resource_root=resource_root)
    effective=replace(plan,**values)
    effective.validate()
    return effective,metadata
