"""真实进程准备免费恶名狩猎和深度追猎，不绑定游戏。"""
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
    marker = args.data / ".autozzz-hunt-test"
    if args.data.exists() and any(args.data.iterdir()) and not marker.is_file():
        raise SystemExit("请使用空目录或本工具已有的测试目录。")
    args.data.mkdir(parents=True, exist_ok=True)
    marker.write_text("isolated-test-profile", encoding="utf-8")
    root = Path(__file__).resolve().parents[1]
    cache = root / "runs/onedragon-20261029/exploration-preparation/assets/models"
    if cache.is_dir(): shutil.copytree(cache, args.data / "assets/models", dirs_exist_ok=True)
    defaults = RuntimePaths.discover(root)
    client = OneDragonClient(RuntimePaths(defaults.source, defaults.python, args.data.resolve()))

    def call(method, **params):
        request = client.submit(method, **params)
        while True:
            try: return client.result(request, timeout=5)
            except RequestPending: pass

    def wait(job):
        while True:
            state = call("execution_status")
            assert state["job_id"] == job, state
            if not state["busy"]: return state
            time.sleep(.1)

    def save(app, **values):
        description = call("describe_configuration", app_id=app)
        return call("save_configuration", app_id=app, values=values, revision=description["revision"])

    try:
        client.start()
        state = wait(call("initialize_execution", server="cn", game_path=GAME_PATHS["cn"])["job_id"])
        assert state["state"] == "ready", state
        save("notorious_hunt", plan_list=[])
        state = wait(call("prepare_application", app_id="notorious_hunt")["job_id"])
        assert state["state"] == "failed" and "计划" in state["error"], state
        options = call("training_options")
        categories = options.get("training", options)
        types = categories["恶名狩猎"]["types"]
        mission_type = next(name for name in types if name != "代理人方案培养")
        missions = types[mission_type]["missions"]
        mission = missions[0]["value"] if missions else None
        plan = dict(tab_name="训练", category_name="恶名狩猎", mission_type_name=mission_type, mission_name=mission,
                    level="默认等级", auto_battle_config="全配队通用", predefined_team_idx=-1, plan_times=3,
                    run_times=0, card_num="默认数量", notorious_hunt_buff_num=1, plan_id=None)
        save("notorious_hunt", plan_list=[plan], loop=True)
        state = wait(call("prepare_application", app_id="notorious_hunt")["job_id"])
        assert state["state"] == "prepared" and not state["uses_charge"], state
        assert state["prepared_requirements"]["uses_hunt"] and "lost_void_det" in state["prepared_requirements"]["models"], state
        weekly = state["prepared_requirements"]
        save("charge_plan", plan_list=[plan], double_reward=False, restore_charge="不使用")
        state = wait(call("prepare_application", app_id="charge_plan")["job_id"])
        assert state["state"] == "prepared" and state["uses_charge"] and state["prepared_requirements"]["uses_hunt"], state
        paid = state["prepared_requirements"]
        description = call("describe_configuration", app_id="_group")
        rows = next(field["value"] for field in description["fields"] if field["name"] == "app_list")
        for row in rows: row["enabled"] = row["app_id"] in {"notorious_hunt", "charge_plan"}
        save("_group", app_list=rows)
        state = wait(call("prepare_application", app_id="one_dragon")["job_id"])
        assert state["state"] == "prepared" and state["uses_charge"], state
        assert {item["id"] for item in state["prepared_applications"]} == {"notorious_hunt", "charge_plan"}, state
        probe = call("runtime_probe")
        assert not probe["window_bound"] and not probe["upstream_hotkeys_started"], probe
        assert "lost_void_det" in probe["detectors"], probe
        print(json.dumps(dict(weekly=weekly, paid=paid, group=state["prepared_requirements"], probe=probe, game_started=False), ensure_ascii=False), flush=True)
    finally:
        while True:
            try: client.close(timeout=5); break
            except RequestPending: pass


if __name__ == "__main__": main()
