"""实际挑战方案和巡逻路线的草稿、校验、原子保存及可恢复归档。"""
from __future__ import annotations

import base64
import copy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
from typing import get_origin
import uuid

from .config_transaction import atomic_apply, config_revision, config_revision_from_path
from .onedragon_library_rules import coordinate, data_tree, library_name, route_selection
from .run_lock import profile_lock

CHALLENGES = {"lost_void_challenge": "迷失之地", "hollow_zero_challenge": "枯萎之都"}
KINDS = {*CHALLENGES, "world_patrol_route_list", "world_patrol_route"}
MAX_BYTES = 768 * 1024


def read_route_groups(root):
    """上游样例文件名可与显示名不同；用户同名文件覆盖样例，不能重复返回。"""
    from one_dragon.utils.yaml_utils import safe_load
    from zzz_od.application.world_patrol.world_patrol_route_list import WorldPatrolRouteList
    root = Path(root).resolve()
    templates, custom, issues = {}, {}, []
    for path in sorted((root / "config/world_patrol_route_list").glob("*.yml")):
        try:
            if not path.resolve().is_relative_to(root) or path.stat().st_size > MAX_BYTES: raise ValueError("路线组路径或大小无效。")
            data = safe_load(path.read_bytes()); data_tree(data)
            if not isinstance(data, dict): raise ValueError("路线组须为记录。")
            group = WorldPatrolRouteList.from_dict(data)
            library_name(group.name)
            if not isinstance(group.route_items, list) or any(not isinstance(row, str) for row in group.route_items) or len(group.route_items) != len(set(group.route_items)):
                raise ValueError("路线组编号须为不重复的文本列表。")
            sample = path.name.endswith(".sample.yml")
            if not sample and path.stem != group.name: raise ValueError("路线组名称与文件不一致。")
            destination = templates if sample else custom
            if group.name in destination: raise ValueError("路线组显示名重复。")
            destination[group.name] = (path, group)
        except Exception as exc: issues.append(dict(file=str(path.relative_to(root)), error=str(exc)[:300]))
    combined = dict(templates, **custom)
    return [combined[name][1] for name in sorted(combined)], {key: value[0] for key, value in templates.items()}, issues


class LibraryStore:
    def __init__(self, service):
        service._assert_idle()
        self.service, self.ctx = service, service.ctx
        self.root, self.source = Path(service.data).resolve(), Path(service.source).resolve()
        with profile_lock(self.root):
            self.world = self.ctx.world_patrol_service
            self.world.load_area()
            self.areas = {area.full_id: area for area in self.world.area_list}
        if len(self.areas) != len(self.world.area_list): raise ValueError("地图区域编号重复。")
        self.issues = []

    def _inside(self, path):
        path = Path(path).resolve()
        if not path.is_relative_to(self.root): raise ValueError("资料路径必须位于本区服数据目录。")
        return path

    def _path(self, kind, key):
        if kind not in KINDS: raise ValueError("资料类型无效。")
        library_name(key)
        if kind == "world_patrol_route":
            area_id, sep, number = key.rpartition("_")
            if not sep or area_id not in self.areas or not number.isascii() or not number.isdecimal() or not 1 <= int(number) <= 999999:
                raise ValueError("路线编号或地图区域无效。")
            area = self.areas[area_id]
            library_name(area.entry.entry_id); library_name(area.full_id)
            return self._inside(self.root / "config" / kind / "system" / area.entry.entry_id / area.full_id / f"{int(number):02d}.yml")
        return self._inside(self.root / "config" / kind / (key + ".yml"))

    def _template(self, kind, key):
        path = self._path(kind, key)
        relative = path.relative_to(self.root)
        if kind in CHALLENGES:
            local = path.with_name(key + ".sample.yml")
            if local.is_file(): return self._inside(local)
            relative = relative.with_name(key + ".sample.yml")
        elif kind == "world_patrol_route_list":
            _, templates, _ = read_route_groups(self.root)
            if key in templates: return self._inside(templates[key])
        original = (self.source / relative).resolve()
        return original if original.is_relative_to(self.source) and original.is_file() else None

    @staticmethod
    def _bytes(path):
        if path.stat().st_size > MAX_BYTES: raise ValueError("资料文件超过允许大小。")
        return path.read_bytes()

    def _revision(self, kind, key):
        path, template = self._path(kind, key), self._template(kind, key)
        digest = hashlib.sha256(str(path).casefold().encode())
        for item in (path, template):
            digest.update(b"\0" + (self._bytes(item) if item is not None and item.is_file() else b"<absent>"))
        return digest.hexdigest()

    def _raw(self, kind, key):
        from one_dragon.utils.yaml_utils import safe_load
        path = self._path(kind, key)
        if not path.is_file(): path = self._template(kind, key)
        if path is None: raise ValueError("资料不存在，请重新读取。")
        try: data = safe_load(self._bytes(path))
        except Exception as exc: raise ValueError("YAML 读取失败：" + type(exc).__name__) from None
        if not isinstance(data, dict): raise ValueError("资料需要 YAML 记录。")
        data_tree(data)
        return data

    def _route_records(self):
        result = []
        from one_dragon.utils.yaml_utils import safe_load
        from zzz_od.application.world_patrol.world_patrol_route import WorldPatrolRoute
        for area in self.areas.values():
            library_name(area.entry.entry_id); library_name(area.full_id)
            directory = self._inside(self.root / "config/world_patrol_route/system" / area.entry.entry_id / area.full_id)
            for path in sorted(directory.glob("*.yml")):
                try:
                    data = safe_load(self._bytes(self._inside(path)))
                    data_tree(data)
                    if not isinstance(data, dict) or type(data.get("idx")) is not int: raise ValueError("路线序号无效。")
                    if not isinstance(data.get("tp_name"), str) or len(data["tp_name"]) > 256: raise ValueError("路线传送点名称无效。")
                    route = WorldPatrolRoute.from_dict(data, area)
                    if self._path("world_patrol_route", route.full_id) != path.resolve() or data.get("tp_area_id") != area.full_id:
                        raise ValueError("路线编号与文件不一致。")
                    result.append(route)
                except Exception as exc: self.issues.append(dict(file=str(path.relative_to(self.root)), error=str(exc)[:300]))
        return sorted(result, key=lambda r: (r.tp_area.entry.entry_id, r.tp_area.full_id, r.idx))

    def _groups(self):
        result, _, issues = read_route_groups(self.root)
        self.issues.extend(issues)
        return result

    def _references(self, kind, key):
        references = []
        if kind == "world_patrol_route":
            previous_issues = len(self.issues)
            for group in self._groups():
                if key in group.route_items: references.append("路线组 · " + group.name)
            if len(self.issues) != previous_issues: raise ValueError("存在无法读取的路线组，暂时不能确认引用。")
        else:
            app, field = ("world_patrol", "route_list") if kind == "world_patrol_route_list" else ("lost_void" if kind == "lost_void_challenge" else "withered_domain", "challenge_config")
            from one_dragon.utils.yaml_utils import safe_load
            for directory in (self.root / "config").iterdir():
                if not directory.is_dir() or not directory.name.isascii() or not directory.name.isdecimal(): continue
                for path in directory.rglob(app + ".yml"):
                    try:
                        data = safe_load(self._bytes(self._inside(path)))
                        data_tree(data)
                        if not isinstance(data, dict) or field in data and not isinstance(data[field], str):
                            raise ValueError("应用引用格式无效。")
                    except Exception: raise ValueError("无法核实引用，请检查：" + str(path.relative_to(self.root))) from None
                    if data.get(field) == key: references.append(str(path.relative_to(self.root)))
        return references

    def _metadata(self, kind, key):
        path, template = self._path(kind, key), self._template(kind, key)
        try: references, reference_error = self._references(kind, key), ""
        except ValueError as exc: references, reference_error = [], str(exc)
        return dict(kind=kind, key=key, name=key, revision=self._revision(kind, key), builtin=template is not None,
                    overridden=template is not None and path.is_file() and self._bytes(path) != self._bytes(template),
                    references=references, reference_error=reference_error)

    def catalog(self):
        self.service._assert_idle()
        with profile_lock(self.root):
            items = []
            for kind in CHALLENGES:
                directory = self.root / "config" / kind
                names = sorted({p.name.removesuffix(".yml").removesuffix(".sample") for p in directory.glob("*.yml")})
                for key in names:
                    try: items.append(self._metadata(kind, key))
                    except Exception as exc: self.issues.append(dict(file=kind + "/" + key, error=str(exc)[:300]))
            for group in self._groups(): items.append(self._metadata("world_patrol_route_list", group.name))
            for route in self._route_records():
                items.append(dict(self._metadata("world_patrol_route", route.full_id), area_id=route.tp_area.full_id,
                                  index=route.idx, name=route.tp_area.full_name + " · " + route.tp_name + f" #{route.idx}", short_name=route.tp_name + f" #{route.idx}"))
            archives = []
            for path in sorted((self.root / "archives/library").glob("*/manifest.json"), reverse=True):
                try:
                    data = self._archive_data(path.parent.name)[0]
                    archives.append(dict(id=path.parent.name, kind=data["kind"], key=data["key"], created_at=data["created_at"]))
                except (ValueError, OSError): continue
            issues = list({row["file"]: row for row in self.issues}.values())
            return dict(items=items, archives=archives, issues=issues,
                        areas=[dict(id=a.full_id, name=a.full_name, entry_id=a.entry.entry_id, entry_name=a.entry.entry_name) for a in self.areas.values()])

    def _map(self, area_id):
        if area_id not in self.areas: raise ValueError("请选择有效地图。")
        from zzz_od.application.world_patrol.world_patrol_area import road_mask_path, icon_yaml_path, WorldPatrolLargeMapIcon, WorldPatrolLargeMap
        from one_dragon.base.config.yaml_operator import YamlOperator
        from one_dragon.utils import cv2_utils
        area = self.areas[area_id]
        path, icon_path = self._inside(road_mask_path(area)), self._inside(icon_yaml_path(area))
        image = cv2_utils.read_image(str(path)) if path.is_file() else None
        if image is None: raise ValueError("所选区域缺少地图资料，暂时不能编辑路线。")
        if image.shape[0] * image.shape[1] > 64_000_000: raise ValueError("地图像素过多。")
        rows = YamlOperator(str(icon_path)).data
        if not isinstance(rows, list): raise ValueError("地图图标数据不完整。")
        icons = [WorldPatrolLargeMapIcon(**row) for row in rows]
        return WorldPatrolLargeMap(area_id, image, icons)

    def map_preview(self, area_id):
        self.service._assert_idle()
        with profile_lock(self.root): return self._map_preview(area_id)

    def _map_preview(self, area_id):
        import cv2
        image = self._map(area_id)
        height, width = image.road_mask.shape[:2]
        ratio = min(1., 1200 / max(width, height))
        preview = cv2.resize(image.road_mask, (max(1, round(width * ratio)), max(1, round(height * ratio))))
        ok, encoded = cv2.imencode(".png", preview)
        if not ok or encoded.nbytes > MAX_BYTES: raise ValueError("地图预览无法编码。")
        return dict(area_id=area_id, width=width, height=height, png=base64.b64encode(encoded).decode(),
                    icons=[i.to_dict() for i in image.icon_list], teleport_points=[i.icon_name for i in image.icon_list if i.template_id == "map_icon_01"])

    @staticmethod
    def _declared_type(config, name):
        expected = getattr(type(config), name).fget.__annotations__.get("return")
        if isinstance(expected, str):
            if expected.startswith(("list[", "List[")): return list
            return {"bool": bool, "int": int, "str": str, "float": float, "list": list}.get(expected)
        return list if get_origin(expected) is list else expected

    def _challenge_fields(self, store, app_id, config):
        fields = store._describe(app_id, config)["fields"]
        for field in fields:
            expected = self._declared_type(config, field["name"])
            if expected is list and not isinstance(field["value"], list):
                field.update(value=[None, None, None] if field["kind"] == "agents" else [], hint="原值类型无效，请重新填写。")
            if expected is list and not field["options"]: field["kind"] = "lines"
            if expected in (bool, int, float, str) and not field["options"]:
                field["kind"] = {bool: "bool", int: "int", float: "float", str: "str"}[expected]
                if expected is bool and type(field["value"]) is not bool:
                    field.update(value=False, hint="原值类型无效，请重新选择。")
        return fields

    def _validate(self, kind, key, data):
        data_tree(data)
        if not isinstance(data, dict): raise ValueError("配置需要记录。")
        data = copy.deepcopy(data)
        if kind in CHALLENGES:
            from .onedragon_config import ConfigurationStore
            store = ConfigurationStore(self.service)
            config = store.get(kind + ":" + key)
            config.data = data
            fields = store._describe(kind + ":" + key, config)["fields"]
            for field in fields:
                expected = self._declared_type(config, field["name"])
                if expected in (bool, int, str, list) and type(field["value"]) is not expected:
                    raise ValueError(field["label"] + "的值类型无效。")
                if expected is float and type(field["value"]) not in (int, float): raise ValueError(field["label"] + "需要数字。")
                store._validate_scalar(field, field["value"])
            if kind == "lost_void_challenge":
                checks = self.ctx.lost_void
                validators = (("artifact_priority", checks.check_artifact_priority_input), ("artifact_priority_2", checks.check_artifact_priority_input),
                              ("region_type_priority", checks.check_region_type_priority_input))
            else:
                from zzz_od.hollow_zero.hollow_zero_data_service import HallowZeroDataService
                checks = HallowZeroDataService(); checks.reload()
                validators = (("resonium_priority", checks.check_resonium_priority), ("go_in_1_step", checks.check_entry_list_input),
                              ("waypoint", checks.check_entry_list_input), ("avoid", checks.check_entry_list_input))
            for name, check in validators:
                cleaned, error = check("\n".join(getattr(config, name)))
                if error: raise ValueError(error)
                data[name] = cleaned
                next(field for field in fields if field["name"] == name)["value"] = cleaned
            return data, config, fields
        if kind == "world_patrol_route_list":
            from zzz_od.application.world_patrol.world_patrol_route_list import WorldPatrolRouteList
            if set(data) != {"name", "list_type", "route_items"} or data["name"] != key: raise ValueError("路线组字段或名称无效。")
            group = WorldPatrolRouteList.from_dict(data)
            route_selection(self._route_records(), group)
            if self.issues: raise ValueError("存在无法读取的路线，请先处理资料错误。")
            return group.to_dict(), None, []
        if set(data) != {"tp_area_id", "tp_name", "idx", "op_list"}: raise ValueError("路线字段无效。")
        if data["tp_area_id"] not in self.areas or type(data["idx"]) is not int or key != f"{data['tp_area_id']}_{data['idx']}": raise ValueError("路线编号与区域不一致。")
        large_map = self._map(data["tp_area_id"])
        if not isinstance(data["tp_name"], str) or data["tp_name"] not in {i.icon_name for i in large_map.icon_list if i.template_id == "map_icon_01"}: raise ValueError("传送点不存在于所选地图。")
        if not isinstance(data["op_list"], list) or len(data["op_list"]) > 3000: raise ValueError("路线最多包含 3000 个移动点。")
        from one_dragon.base.geometry.point import Point
        from zzz_od.application.world_patrol.world_patrol_route import WorldPatrolRoute
        route = WorldPatrolRoute(self.areas[data["tp_area_id"]], data["tp_name"], data["idx"])
        height, width = large_map.road_mask.shape[:2]
        for row in data["op_list"]:
            if not isinstance(row, dict) or set(row) != {"op_type", "data"} or row["op_type"] != "move" or not isinstance(row["data"], list) or len(row["data"]) != 2:
                raise ValueError("移动点需要操作类型及两个坐标。")
            route.add_move_operation(Point(coordinate(row["data"][0], width), coordinate(row["data"][1], height)))
        return route.to_dict(), None, []

    def _document(self, kind, key, data=None, *, draft=False):
        raw = self._raw(kind, key) if data is None else data
        data_tree(raw)
        fields = []
        if kind in CHALLENGES:
            from .onedragon_config import ConfigurationStore
            store = ConfigurationStore(self.service)
            config = store.get(kind + ":" + key); config.data = copy.deepcopy(raw)
            fields = self._challenge_fields(store, kind + ":" + key, config)
        metadata = self._metadata(kind, key)
        if kind == "world_patrol_route" and raw.get("tp_area_id") in self.areas:
            metadata["name"] = self.areas[raw["tp_area_id"]].full_name + " · " + str(raw.get("tp_name", "")) + f" #{raw.get('idx', '')}"
        return dict(metadata, data=raw, fields=fields, draft=draft)

    def read(self, kind, key):
        self.service._assert_idle()
        with profile_lock(self.root): return self._document(kind, key)

    def draft(self, kind, key="", source_key="", source_revision="", area_id="", text=None, data=None):
        self.service._assert_idle()
        with profile_lock(self.root):
            if data is not None:
                data_tree(data)
                if not isinstance(data, dict): raise ValueError("复制草稿须为记录。")
            if kind == "world_patrol_route":
                if area_id not in self.areas: raise ValueError("请先选择地图。")
                index = self.world.get_next_route_idx(self.areas[area_id])
                key = f"{area_id}_{index}"
            path = self._path(kind, key)
            if path.exists() or self._template(kind, key) is not None: raise ValueError("名称或路线编号已经存在，请换一个。")
            if source_key:
                if self._revision(kind, source_key) != source_revision: raise ValueError("来源已变化，请重新读取后复制。")
                raw = copy.deepcopy(data) if data is not None else self._raw(kind, source_key)
            elif data is not None:
                raw = copy.deepcopy(data)
            elif text is not None:
                if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_BYTES: raise ValueError("导入文件过大。")
                from one_dragon.utils.yaml_utils import safe_load
                try: raw = safe_load(text)
                except Exception as exc: raise ValueError("导入 YAML 格式错误：" + type(exc).__name__) from None
                data_tree(raw)
                if not isinstance(raw, dict): raise ValueError("导入内容须为记录。")
            elif kind in CHALLENGES: raw = {}
            elif kind == "world_patrol_route_list": raw = dict(name=key, list_type="whitelist", route_items=[])
            else:
                preview = self._map_preview(area_id)
                raw = dict(tp_area_id=area_id, tp_name=next(iter(preview["teleport_points"]), ""), idx=index, op_list=[])
            if kind == "world_patrol_route_list": raw["name"] = key
            elif kind == "world_patrol_route": raw.update(tp_area_id=area_id, idx=index)
            result = self._document(kind, key, raw, draft=True)
            if source_key: result["source"] = dict(key=source_key, revision=source_revision)
            return result

    def _write(self, path, raw, revision, guard):
        import yaml
        path = self._inside(path)
        content = yaml.safe_dump(raw, allow_unicode=True, sort_keys=False).encode("utf-8")
        if len(content) > MAX_BYTES: raise ValueError("保存内容过大。")
        temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            temporary.write_bytes(content)
            if config_revision_from_path(path) != revision or not guard(): raise ValueError("资料已变化，请重新读取后保存。")
            os.replace(temporary, path)
        finally: temporary.unlink(missing_ok=True)

    def save(self, kind, key, data, revision, source_key="", source_revision=""):
        self.service._assert_idle()
        with profile_lock(self.root):
            from .map_transaction import MapTransaction
            MapTransaction(self.root).assert_clear()
            return self._save(kind, key, data, revision, source_key, source_revision)

    def _save(self, kind, key, data, revision, source_key="", source_revision=""):
        if self._revision(kind, key) != revision: raise ValueError("资料已变化，请重新读取后保存。")
        if source_key and self._revision(kind, source_key) != source_revision: raise ValueError("复制来源已变化，请重新读取。")
        raw, config, fields = self._validate(kind, key, data)
        path = self._path(kind, key)
        if config is not None:
            def apply():
                if self._revision(kind, key) != revision: raise ValueError("来源已变化。")
                for field in fields: setattr(config, field["name"], field["value"])
            atomic_apply(config, config_revision(config), apply, self.root,
                         guard=lambda: self._revision(kind, key) == revision and (not source_key or self._revision(kind, source_key) == source_revision))
        else: self._write(path, raw, config_revision_from_path(path),
                         lambda: self._revision(kind, key) == revision and (not source_key or self._revision(kind, source_key) == source_revision))
        self._refresh()
        return self._document(kind, key)

    def _refresh(self):
        from one_dragon.base.config.yaml_operator import cached_yaml_data
        cached_yaml_data.clear()
        self.ctx.run_context.clear_application_cache()

    def _archive(self, kind, key, raw):
        identifier = uuid.uuid4().hex
        directory = self._inside(self.root / "archives/library" / identifier)
        directory.mkdir(parents=True, exist_ok=False)
        (directory / "record.yml").write_bytes(raw)
        (directory / "manifest.json").write_text(json.dumps(dict(kind=kind, key=key, sha256=hashlib.sha256(raw).hexdigest(), created_at=datetime.now().astimezone().isoformat()), ensure_ascii=False), encoding="utf-8")
        return identifier

    def remove(self, kind, key, revision):
        self.service._assert_idle()
        with profile_lock(self.root):
            from .map_transaction import MapTransaction
            MapTransaction(self.root).assert_clear()
            path, template = self._path(kind, key), self._template(kind, key)
            if not path.is_file(): raise ValueError("内置原件不需要恢复；可先另存为自定义资料。")
            if self._revision(kind, key) != revision: raise ValueError("资料已变化，请重新读取。")
            refs = self._references(kind, key)
            if template is None and refs: raise ValueError("仍被引用，请先更换：" + "、".join(refs))
            raw = self._bytes(path)
            archive = self._archive(kind, key, raw)
            if self._revision(kind, key) != revision: raise ValueError("归档期间资料发生变化，原件保持不变。")
            if template is not None:
                replacement = self._bytes(template)
                temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
                try:
                    temporary.write_bytes(replacement)
                    if self._revision(kind, key) != revision: raise ValueError("恢复期间资料发生变化，原件保持不变。")
                    os.replace(temporary, path)
                finally: temporary.unlink(missing_ok=True)
            else: path.unlink()
            self._refresh()
            return dict(archive_id=archive, restored_template=template is not None)

    def _archive_data(self, identifier):
        if not isinstance(identifier, str) or len(identifier) != 32 or any(c not in "0123456789abcdef" for c in identifier): raise ValueError("归档编号无效。")
        directory = self._inside(self.root / "archives/library" / identifier)
        manifest = json.loads(self._bytes(directory / "manifest.json"))
        if not isinstance(manifest, dict) or set(manifest) != {"kind", "key", "sha256", "created_at"}: raise ValueError("归档信息无效。")
        self._path(manifest["kind"], manifest["key"])
        raw = self._bytes(directory / "record.yml")
        if hashlib.sha256(raw).hexdigest() != manifest["sha256"]: raise ValueError("归档内容已变化。")
        return manifest, raw

    def archive_read(self, identifier):
        self.service._assert_idle()
        with profile_lock(self.root):
            from one_dragon.utils.yaml_utils import safe_load
            manifest, raw = self._archive_data(identifier)
            data = safe_load(raw); data_tree(data)
            result = self._document(manifest["kind"], manifest["key"], data, draft=True)
            result["archive_id"] = identifier
            return result

    def export(self, kind, key, revision):
        self.service._assert_idle()
        with profile_lock(self.root):
            if self._revision(kind, key) != revision: raise ValueError("资料已变化，请重新读取后导出。")
            import yaml
            return dict(text=yaml.safe_dump(self._raw(kind, key), allow_unicode=True, sort_keys=False))

    def rename(self, kind, key, name, revision):
        self.service._assert_idle()
        with profile_lock(self.root):
            from .map_transaction import MapTransaction
            MapTransaction(self.root).assert_clear()
            if kind == "world_patrol_route": raise ValueError("路线使用地图与序号标识；跨地图请复制路线。")
            if self._template(kind, key) is not None: raise ValueError("内置资料请使用另存为。")
            if self._revision(kind, key) != revision: raise ValueError("资料已变化，请重新读取。")
            references = self._references(kind, key)
            if references: raise ValueError("仍被引用，请先更换：" + "、".join(references))
            target = self._path(kind, name)
            if target.exists() or self._template(kind, name) is not None: raise ValueError("新名称已经存在。")
            raw = self._raw(kind, key)
            if kind == "world_patrol_route_list": raw["name"] = name
            archive = self._archive(kind, key, self._bytes(self._path(kind, key)))
            result = self._save(kind, name, raw, self._revision(kind, name), key, revision)
            # 若发布后旧件被外部改动，保留两份并准确报告，不删除未知的新数据。
            if self._revision(kind, key) != revision or self._references(kind, key):
                result["notice"] = "新名称已保存，旧资料发生变化或仍被引用，已保留旧资料。"
            else:
                try: self._path(kind, key).unlink()
                except OSError: result["notice"] = "新名称已保存，旧资料暂未移除。"
            self._refresh()
            return dict(result, archive_id=archive)
