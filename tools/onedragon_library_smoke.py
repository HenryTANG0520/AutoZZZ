"""实际方案、地图与巡逻路线管理回放，不初始化控制器或访问游戏。"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import copy
import json
from pathlib import Path
import platform
import sys
import uuid
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    args = parser.parse_args()
    marker = args.data / ".autozzz-library-test"
    if args.data.exists() and any(args.data.iterdir()) and not marker.is_file(): raise SystemExit("请使用空目录或本工具拥有的目录。")
    args.data.mkdir(parents=True, exist_ok=True)
    marker.write_text("isolated actual SDK library test", encoding="utf-8")
    platform.uname()
    from autozzz.onedragon_runtime import RuntimePaths
    defaults = RuntimePaths.discover(Path(__file__).resolve().parents[1])
    RuntimePaths(defaults.source, defaults.python, args.data.resolve()).prepare()
    sys.path.insert(0, str(defaults.source / "src"))
    from autozzz.onedragon_service import OneDragonService
    from autozzz.onedragon_library import LibraryStore
    from autozzz.onedragon_patrol import PatrolOrderControl
    from zzz_od.application.world_patrol.world_patrol_route_list import WorldPatrolRouteList
    from zzz_od.application.world_patrol.world_patrol_app import WorldPatrolApp
    from one_dragon.base.operation.operation_round_result import OperationRoundResultEnum
    checks = []
    tag, renamed_names = "fixture-" + uuid.uuid4().hex[:8], {}
    def forbidden(*_, **__): raise AssertionError("离线资料检查禁止外部操作")
    with ExitStack() as blocks:
        for target in ("subprocess.Popen", "winreg.SetValueEx", "winreg.DeleteValue", "requests.sessions.Session.request", "socket.socket.connect", "socket.socket.connect_ex", "socket.getaddrinfo"):
            blocks.enter_context(patch(target, side_effect=forbidden))
        service = OneDragonService(defaults.source, args.data.resolve())
        try:
            catalog = service.library_catalog()
            print(json.dumps(dict(initial_items=len(catalog["items"]), issues=catalog["issues"][:10]), ensure_ascii=False), file=sys.stderr)
            assert not catalog["issues"], catalog["issues"][:3]
            route_item = next(row for row in catalog["items"] if row["kind"] == "world_patrol_route")
            original_route = service.library_read(route_item["kind"], route_item["key"])
            preview = service.library_map(route_item["area_id"])
            assert preview["png"] and preview["width"] > 10 and preview["height"] > 10
            checks.append("actual_library_catalog_and_original_map_preview_without_ocr_or_controller")
            for kind in ("lost_void_challenge", "hollow_zero_challenge"):
                source = next(row for row in catalog["items"] if row["kind"] == kind and row["builtin"])
                original = service.library_read(kind, source["key"])
                draft = service.library_draft(kind, key=tag + "-" + kind, source_key=source["key"], source_revision=original["revision"])
                target = args.data / "config" / kind / (draft["key"] + ".yml")
                assert not target.exists()
                saved = service.library_save(kind, draft["key"], draft["data"], draft["revision"], source["key"], original["revision"])
                assert target.is_file() and saved["data"]
                assert service.library_read(kind, source["key"])["data"] == original["data"]
                archive = service.library_remove(kind, saved["key"], saved["revision"])
                assert not target.exists()
                restored = service.library_archive_read(archive["archive_id"])
                assert restored["draft"] and not target.exists()
                saved = service.library_save(kind, restored["key"], restored["data"], restored["revision"])
                exported = service.library_export(kind, saved["key"], saved["revision"])
                imported = service.library_draft(kind, key=tag + "-import-" + kind, text=exported["text"])
                assert imported["data"] == saved["data"]
                renamed_names[kind] = tag + "-renamed-" + kind
                renamed = service.library_rename(kind, saved["key"], renamed_names[kind], saved["revision"])
                assert not target.exists() and (args.data / "config" / kind / (renamed["key"] + ".yml")).exists()
                # 自定义方案被真实应用配置引用时，移除和改名均须保留原件。
                app_id = "lost_void" if kind == "lost_void_challenge" else "withered_domain"
                description = service.describe_configuration(app_id)
                previous = next(f["value"] for f in description["fields"] if f["name"] == "challenge_config")
                selected = service.save_configuration(app_id, dict(challenge_config=renamed["key"]), description["revision"])
                for action in (lambda: service.library_remove(kind, renamed["key"], renamed["revision"]),
                               lambda: service.library_rename(kind, renamed["key"], tag + "-blocked", renamed["revision"])):
                    try: action(); raise AssertionError("仍被引用的方案被移除或改名")
                    except ValueError as exc: assert "引用" in str(exc)
                service.save_configuration(app_id, dict(challenge_config=previous), selected["revision"])
            checks.append("actual_both_challenge_classes_copy_import_export_rename_archive_restore_preserve_templates")
            checks.append("actual_application_references_prevent_challenge_rename_or_archive")

            builtin = next(row for row in catalog["items"] if row["kind"] == "lost_void_challenge" and row["builtin"])
            original = service.library_read(builtin["kind"], builtin["key"])
            tree = copy.deepcopy(original["data"])
            switch = next(f for f in original["fields"] if f["kind"] == "bool")
            tree[switch["name"]] = not switch["value"]
            saved = service.library_save(builtin["kind"], builtin["key"], tree, original["revision"])
            assert saved["overridden"]
            restored = service.library_remove(builtin["kind"], builtin["key"], saved["revision"])
            assert restored["restored_template"] and service.library_read(builtin["kind"], builtin["key"])["data"] == original["data"]
            checks.append("builtin_challenge_local_override_restores_exact_template_semantics_and_keeps_archive")

            route_draft = service.library_draft("world_patrol_route", area_id=route_item["area_id"], source_key=route_item["key"], source_revision=original_route["revision"])
            saved_route = service.library_save("world_patrol_route", route_draft["key"], route_draft["data"], route_draft["revision"])
            route_keys = [saved_route["key"], route_item["key"]]
            group_draft = service.library_draft("world_patrol_route_list", key=tag + "-route-order")
            group_draft["data"]["route_items"] = route_keys
            group = service.library_save(group_draft["kind"], group_draft["key"], group_draft["data"], group_draft["revision"])
            rows = service.ctx.world_patrol_service.get_world_patrol_routes()
            actual_group = next(row for row in service.ctx.world_patrol_service.get_world_patrol_route_lists() if row.name == group["key"])
            assert actual_group.route_items == route_keys and any(row.full_id == saved_route["key"] for row in rows)
            try:
                service.library_remove("world_patrol_route", saved_route["key"], saved_route["revision"])
                raise AssertionError("仍被引用的路线被移除")
            except ValueError as exc: assert "引用" in str(exc)
            checks.append("actual_route_and_group_domain_roundtrip_and_route_reference_guard")

            original_node = WorldPatrolApp.init_world_patrol
            control = PatrolOrderControl(service.ctx)
            control.install()
            try:
                app = WorldPatrolApp(service.ctx)
                app.config.route_list = group["key"]
                with patch.object(service.ctx.auto_battle_context, "init_auto_op"):
                    app._init_network()
                    assert app._node_map['初始化'].op_method is WorldPatrolApp.init_world_patrol
                    result = app._node_map['初始化'].op_method(app)
                    assert result.result == OperationRoundResultEnum.SUCCESS
                    assert [row.full_id for row in app.route_list] == route_keys
                    app.init_world_patrol()
                    assert [row.full_id for row in app.route_list] == route_keys
                    from autozzz.onedragon_requirements import ApplicationRequirements
                    requirements = ApplicationRequirements(service.ctx, args.data, None)
                    requirements._world_patrol(app.config)
                    assert requirements.routes == route_keys
                    blacklist = dict(group["data"], list_type="blacklist")
                    group = service.library_save(group["kind"], group["key"], blacklist, group["revision"])
                    app.init_world_patrol()
                    expected = [route.full_id for route in service.ctx.world_patrol_service.get_world_patrol_routes() if route.full_id not in route_keys]
                    assert [route.full_id for route in app.route_list] == expected
                    requirements._world_patrol(app.config)
                    assert requirements.routes == expected
                    group = service.library_save(group["kind"], group["key"], dict(group["data"], list_type="whitelist"), group["revision"])
                # 对应的配置选择必须阻止改名/删除路线组。
                group = service.library_read(group["kind"], group["key"])
                try: service.library_remove(group["kind"], group["key"], group["revision"]); raise AssertionError("引用的路线组被移除")
                except ValueError as exc: assert "引用" in str(exc)
                app.config.route_list = ""
            finally: control.close()
            assert WorldPatrolApp.init_world_patrol is original_node
            checks.append("actual_patrol_initialization_uses_saved_whitelist_order_idempotently_and_restores_node")
            checks.append("actual_operation_graph_and_preparation_match_white_order_and_black_exclusion")

            template_group = next(row for row in catalog["items"] if row["kind"] == "world_patrol_route_list" and row["builtin"])
            original_group = service.library_read(template_group["kind"], template_group["key"])
            override = dict(original_group["data"], route_items=list(reversed(original_group["data"]["route_items"])))
            saved = service.library_save(template_group["kind"], template_group["key"], override, original_group["revision"])
            effective = [g for g in service.ctx.world_patrol_service.get_world_patrol_route_lists() if g.name == saved["key"]]
            assert len(effective) == 1 and effective[0].route_items == override["route_items"]
            service.library_remove(saved["kind"], saved["key"], saved["revision"])
            effective = [g for g in service.ctx.world_patrol_service.get_world_patrol_route_lists() if g.name == saved["key"]]
            assert len(effective) == 1 and effective[0].route_items == original_group["data"]["route_items"]
            checks.append("sample_filename_aliases_and_user_overrides_produce_one_effective_route_group")

            raw_before = {p: p.read_bytes() for p in (args.data / "config").rglob("*.yml")}
            for changes in (dict(saved_route["data"], tp_name="unknown"), dict(saved_route["data"], op_list=[dict(op_type="move", data=[-1, 2])]),
                            dict(saved_route["data"], op_list=[dict(op_type="unsupported", data=[1, 2])])):
                try: service.library_save("world_patrol_route", saved_route["key"], changes, saved_route["revision"]); raise AssertionError("无效路线未拒绝")
                except ValueError: pass
            try: service.library_save(group["kind"], group["key"], group["data"], "stale"); raise AssertionError("旧版本未拒绝")
            except ValueError: pass
            for key in ("../outside", "CON", "a\nb", "space "):
                try: service.library_draft("lost_void_challenge", key=key); raise AssertionError("无效名称未拒绝")
                except ValueError: pass
            assert all(path.read_bytes() == data for path, data in raw_before.items())
            checks.append("invalid_coordinates_teleport_operations_path_and_stale_revision_preserve_existing_files")
            target = LibraryStore(service)._path('world_patrol_route', saved_route['key'])
            previous_bytes = target.read_bytes()
            changed = copy.deepcopy(saved_route['data'])
            changed['op_list'].append(copy.deepcopy(changed['op_list'][0]))
            with patch('autozzz.onedragon_library.os.replace',side_effect=OSError('fixture file locked')):
                try: service.library_save('world_patrol_route',saved_route['key'],changed,saved_route['revision']); raise AssertionError('写入失败未上报')
                except OSError: pass
            assert target.read_bytes()==previous_bytes and not list(target.parent.glob('*.tmp'))
            checks.append('failed_route_publish_keeps_original_file_and_removes_temporary_file')

            bad_group = args.data/'config/world_patrol_route_list/fixture-invalid.yml'
            bad_group.write_text('not: a route group\n',encoding='utf-8')
            try:
                diagnostic = service.library_catalog()
                assert diagnostic['items'] and any(row['file'].endswith('fixture-invalid.yml') for row in diagnostic['issues'])
                try: service.library_remove('world_patrol_route',saved_route['key'],saved_route['revision']); raise AssertionError('无法核实引用时仍移除路线')
                except ValueError as exc: assert '引用' in str(exc)
            finally: bad_group.unlink()
            checks.append('invalid_group_has_visible_diagnostic_and_blocks_unverified_reference_removal')

            # 账号编号超过两位时也必须检查引用，不能仅扫描 01—99。
            reference=args.data/'config/100/one_dragon/lost_void.yml'
            reference.parent.mkdir(parents=True,exist_ok=True)
            reference.write_text('challenge_config: '+renamed_names['lost_void_challenge']+'\n',encoding='utf-8')
            try:
                doc=service.library_read('lost_void_challenge',renamed_names['lost_void_challenge'])
                assert any('100' in row for row in doc['references'])
                try: service.library_remove(doc['kind'],doc['key'],doc['revision']); raise AssertionError('遗漏三位账号编号引用')
                except ValueError as exc: assert '引用' in str(exc)
            finally: reference.unlink()
            checks.append('three_digit_account_reference_prevents_challenge_removal')

            for invalid in ('[]\n', 'challenge_config: []\n', 'challenge_config: &cycle [*cycle]\n'):
                reference.write_text(invalid, encoding='utf-8')
                try:
                    doc=service.library_read('lost_void_challenge',renamed_names['lost_void_challenge'])
                    assert doc['reference_error']
                    try: service.library_remove(doc['kind'],doc['key'],doc['revision']); raise AssertionError('损坏账号引用仍可移除方案')
                    except ValueError as exc: assert '引用' in str(exc)
                finally: reference.unlink()
            checks.append('malformed_account_reference_blocks_removal_instead_of_assuming_unreferenced')

            write_original=LibraryStore._write
            for source_document in (original_route, group):
                kind=source_document['kind']; source_path=LibraryStore(service)._path(kind,source_document['key'])
                source_bytes=source_path.read_bytes()
                draft=service.library_draft(kind,key=tag+'-conflict-copy',area_id=route_item['area_id'],
                                             source_key=source_document['key'],source_revision=source_document['revision'])
                target=LibraryStore(service)._path(kind,draft['key'])
                def edit_source_before_publish(store, path, raw, revision, guard):
                    source_path.write_bytes(source_bytes+b'\n# changed during copy\n')
                    return write_original(store,path,raw,revision,guard)
                try:
                    with patch.object(LibraryStore,'_write',edit_source_before_publish):
                        try:
                            service.library_save(kind,draft['key'],draft['data'],draft['revision'],source_document['key'],source_document['revision'])
                            raise AssertionError('复制期间来源变化仍然发布')
                        except ValueError as exc: assert '变化' in str(exc)
                    assert not target.exists() and not list(target.parent.glob('*.tmp'))
                finally: source_path.write_bytes(source_bytes)
            checks.append('route_and_group_copy_recheck_source_at_publish_and_preserve_conflicting_draft')

            source=service.library_read('lost_void_challenge',renamed_names['lost_void_challenge'])
            switch=next(f for f in source['fields'] if f['kind']=='bool')
            malformed=dict(source['data'],**{switch['name']:'false'})
            repair=service.library_draft(source['kind'],key=tag+'-repair',data=malformed)
            repair_field=next(f for f in repair['fields'] if f['name']==switch['name'])
            assert repair_field['kind']=='bool' and repair_field['hint'] and repair_field['value'] is False
            try:
                service.library_save(source['kind'],source['key'],malformed,source['revision'])
                raise AssertionError('字符串被当作有效开关写入')
            except ValueError: pass
            edited=dict(source['data'],artifact_priority=['  测试自定义鸣徽  ',''])
            draft=service.library_draft(source['kind'],key=tag+'-edited-copy',source_key=source['key'],source_revision=source['revision'],data=edited)
            assert draft['data']['artifact_priority']==edited['artifact_priority']
            saved=service.library_save(draft['kind'],draft['key'],draft['data'],draft['revision'],source['key'],source['revision'])
            assert saved['data']['artifact_priority']==['测试自定义鸣徽']
            archived=service.library_remove(saved['kind'],saved['key'],saved['revision'])
            record=args.data/'archives/library'/archived['archive_id']/'record.yml'
            record_bytes=record.read_bytes();record.write_bytes(record_bytes+b'\n# altered\n')
            try:
                try: service.library_archive_read(archived['archive_id']); raise AssertionError('损坏归档仍可恢复')
                except ValueError as exc: assert '变化' in str(exc)
            finally: record.write_bytes(record_bytes)
            checks.append('edited_copy_uses_upstream_priority_normalization_and_archive_hash_is_checked')
            assert service.ctx.controller is None and service.ctx.ocr._model is None and not service.ctx._btn_listener_started
            snapshot = dict(catalog=service.library_catalog(), route=service.library_read("world_patrol_route", saved_route["key"]), group=group, map=preview,
                            challenge=service.library_read("lost_void_challenge", renamed_names["lost_void_challenge"]),
                            hollow_challenge=service.library_read("hollow_zero_challenge", renamed_names["hollow_zero_challenge"]), repair=repair)
            (args.data / "ui-descriptions.json").write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
            print(json.dumps(dict(checks=checks, count=len(checks), game_accessed=False, external_operations_blocked=True), ensure_ascii=False))
        finally: service.close()


if __name__ == "__main__": main()
