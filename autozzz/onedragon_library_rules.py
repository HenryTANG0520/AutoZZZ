"""路线与方案的纯数据约束，编辑和执行共用同一选择规则。"""
from __future__ import annotations

import math


def library_name(value):
    if (not isinstance(value, str) or not value or value != value.strip() or len(value) > 100
        or value in (".", "..") or value.endswith((".", " ", ".sample", ".merged"))
        or any(ord(c) < 32 or c in '/\\<>:"|?*' for c in value)
        or value.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]}):
        raise ValueError("名称须为 1—100 字，不能含路径或 Windows 文件名保留字符。")
    return value


def data_tree(value, *, limit=30000):
    """只接收有界 JSON 数据树，拒绝 YAML 循环引用和隐式日期等非配置值。"""
    count, active = 0, set()
    def visit(item, depth):
        nonlocal count
        count += 1
        if count > limit or depth > 24: raise ValueError("配置层级或条目过多。")
        if item is None or type(item) in (bool, int, str):
            if isinstance(item, str) and (len(item) > 65536 or "\0" in item): raise ValueError("配置文本过长或包含空字符。")
            return
        if type(item) is float and math.isfinite(item): return
        if type(item) not in (dict, list): raise ValueError("配置只能包含文本、数字、开关、列表和记录。")
        if id(item) in active: raise ValueError("配置不能包含循环引用。")
        active.add(id(item))
        try:
            if isinstance(item, dict):
                for key, entry in item.items():
                    if not isinstance(key, str) or len(key) > 256: raise ValueError("配置字段名须为文本。")
                    visit(entry, depth + 1)
            else:
                for entry in item: visit(entry, depth + 1)
        finally: active.remove(id(item))
    visit(value, 0)


def route_selection(routes, group=None):
    by_id = {route.full_id: route for route in routes}
    if len(by_id) != len(routes): raise ValueError("存在重复的路线编号。")
    if group is None: return list(routes)
    if group.list_type not in ("whitelist", "blacklist"): raise ValueError("路线组类型无效。")
    rows = group.route_items
    if not isinstance(rows, list) or any(not isinstance(key, str) for key in rows) or len(rows) != len(set(rows)):
        raise ValueError("路线组包含重复或无效编号。")
    missing = set(rows) - set(by_id)
    if missing: raise ValueError("路线组包含不存在的路线：" + "、".join(sorted(missing)))
    return [by_id[key] for key in rows] if group.list_type == "whitelist" else [route for route in routes if route.full_id not in rows]


def coordinate(value, maximum):
    if isinstance(value, str) and value.isascii() and value.isdecimal(): value = int(value)
    if type(value) is not int or not 0 <= value < maximum: raise ValueError("坐标须为地图范围内的整数像素。")
    return value
