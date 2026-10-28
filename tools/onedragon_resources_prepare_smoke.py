"""准备实际经营与拆解应用，检查行为清单和一次性确认；不绑定游戏。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from autozzz.config import GAME_PATHS
from autozzz.onedragon_runtime import OneDragonClient, RequestPending, RuntimePaths


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    args = parser.parse_args()
    marker = args.data / ".autozzz-resources-test"
    if args.data.exists() and any(args.data.iterdir()) and not marker.is_file():
        raise SystemExit("请使用空目录或本工具已有的测试目录。")
    args.data.mkdir(parents=True, exist_ok=True)
    marker.write_text("isolated-test-profile", encoding="utf-8")
    root = Path(__file__).resolve().parents[1]
    source = root / "runs/onedragon-20261029/tool-preparation/assets/models"
    if source.is_dir():
        shutil.copytree(source, args.data / "assets/models", dirs_exist_ok=True)
    defaults = RuntimePaths.discover(root)
    client = OneDragonClient(RuntimePaths(defaults.source, defaults.python, args.data.resolve()))

    def call(method: str, **params):
        request = client.submit(method, **params)
        while True:
            try: return client.result(request, timeout=5)
            except RequestPending: pass

    def wait(job: str):
        while True:
            state = call("execution_status")
            assert state["job_id"] == job, state
            if not state["busy"]: return state
            time.sleep(.1)

    def save(app: str, **values):
        description = call("describe_configuration", app_id=app)
        return call("save_configuration", app_id=app, values=values, revision=description["revision"])

    try:
        client.start()
        state = wait(call("initialize_execution", server="cn", game_path=GAME_PATHS["cn"])["job_id"])
        assert state["state"] == "ready", state
        save("suibian_temple", auto_manage_enabled=True, good_goods_purchase_enabled=False, boo_box_purchase_enabled=False)
        state = wait(call("prepare_application", app_id="suibian_temple")["job_id"])
        assert state["state"] == "prepared" and not state["resources"]["confirmation_required"], state
        assert [item["id"] for item in state["resources"]["actions"]] == ["suibian_temple:hosting"], state
        description = call("describe_configuration", app_id="suibian_temple")
        assert all(not field.get("supported_in_runtime", True) for field in description["fields"] if field["name"].startswith("pawnshop_"))
        save("suibian_temple", auto_manage_enabled=False, good_goods_purchase_enabled=True, boo_box_purchase_enabled=True)
        state = wait(call("prepare_application", app_id="suibian_temple")["job_id"])
        assert state["state"] == "prepared" and state["resources"]["confirmation_required"], state
        manual = state["resources"]
        assert len(manual["actions"]) == 4 and all(item["requires_confirmation"] for item in manual["actions"]), manual

        save("drive_disc_dismantle", dismantle_level="A及以下", dismantle_abandon=True)
        state = wait(call("prepare_application", app_id="drive_disc_dismantle")["job_id"])
        assert state["state"] == "prepared" and state["resources"]["confirmation_required"], state
        assert "额外包含全部已弃置" in state["resources"]["actions"][0]["text"], state
        old = state["resources"]["confirmation_id"]
        for token in (None, "obsolete-confirmation"):
            try: call("start_application", app_id="drive_disc_dismantle", resource_confirmation=token)
            except Exception as exc: assert "确认" in str(exc), exc
            else: raise AssertionError("未确认就开始拆解")
        assert call("execution_status")["state"] == "prepared"
        state = wait(call("prepare_application", app_id="drive_disc_dismantle")["job_id"])
        assert state["resources"]["confirmation_id"] != old, state
        try: call("start_application", app_id="drive_disc_dismantle", resource_confirmation=old)
        except Exception as exc: assert "确认" in str(exc), exc
        else: raise AssertionError("重新准备后仍接受旧确认")

        description = call("describe_configuration", app_id="_group")
        rows = next(field["value"] for field in description["fields"] if field["name"] == "app_list")
        for row in rows: row["enabled"] = row["app_id"] in {"suibian_temple", "drive_disc_dismantle"}
        save("_group", app_list=rows)
        state = wait(call("prepare_application", app_id="one_dragon")["job_id"])
        assert state["state"] == "prepared" and len(state["resources"]["actions"]) == 5, state
        assert set(state["prepared_requirements"]["resource_settings"]) == {"suibian_temple", "drive_disc_dismantle"}, state
        probe = call("runtime_probe")
        assert not probe["window_bound"] and not probe["upstream_hotkeys_started"], probe
        print(json.dumps(dict(manual=manual, group=state["resources"], prepared_applications=state["prepared_applications"], probe=probe, game_started=False), ensure_ascii=False), flush=True)
    finally:
        while True:
            try: client.close(timeout=5); break
            except RequestPending: pass


if __name__ == "__main__": main()
