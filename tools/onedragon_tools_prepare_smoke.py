"""用真实独立进程准备工具应用，不绑定游戏或执行输入。"""
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
    root = Path(__file__).resolve().parents[1]
    marker = args.data / ".autozzz-tools-test"
    if args.data.exists() and any(args.data.iterdir()) and not marker.is_file():
        raise SystemExit("请使用空目录或本工具已有的测试目录。")
    args.data.mkdir(parents=True, exist_ok=True)
    marker.write_text("isolated-test-profile", encoding="utf-8")
    cache = root / "runs/onedragon-20261029/exploration-preparation/assets/models"
    if cache.is_dir():
        shutil.copytree(cache, args.data / "assets/models", dirs_exist_ok=True)
    defaults = RuntimePaths.discover(root)
    paths = RuntimePaths(defaults.source, defaults.python, args.data.resolve())
    client = OneDragonClient(paths, log=lambda message: print(message, file=sys.stderr, flush=True))

    def call(method: str, **params):
        request = client.submit(method, **params)
        while True:
            try:
                return client.result(request, timeout=5)
            except RequestPending:
                pass

    def wait(job: str):
        last = None
        while True:
            state = call("execution_status")
            assert state["job_id"] == job, state
            phase = state.get("preparation_step") or state["state"]
            if phase != last:
                print(json.dumps(dict(phase=phase), ensure_ascii=False), flush=True)
                last = phase
            if not state["busy"]:
                return state
            time.sleep(.1)

    try:
        client.start()
        state = wait(call("initialize_execution", server="cn", game_path=GAME_PATHS["cn"])["job_id"])
        assert state["state"] == "ready", state
        results = {}
        for app in ("redemption_code", "commission_assistant", "operation_debug", "life_on_line"):
            state = wait(call("prepare_application", app_id=app)["job_id"])
            assert state["state"] == "prepared" and state["prepared_app"] == app, state
            assert not state["uses_charge"], state
            results[app] = state["prepared_requirements"]
        assert results["operation_debug"]["scripts"][0]["operations"] > 0, results
        assert results["life_on_line"]["scripts"][0]["operations"] == 14, results
        assert results["commission_assistant"]["shortcuts"] == dict(dodge="5", battle="6"), results
        assert "hollow_zero_event" in results["commission_assistant"]["models"], results

        description = call("describe_configuration", app_id="commission_assistant")
        try:
            call("save_configuration", app_id="commission_assistant", values=dict(dodge_switch="F9"), revision=description["revision"])
        except Exception as exc:
            assert "F9" in str(exc), str(exc)
        else:
            raise AssertionError("F9 冲突未拒绝")
        assert call("describe_configuration", app_id="commission_assistant")["revision"] == description["revision"]

        description = call("describe_configuration", app_id="_group")
        rows = next(field["value"] for field in description["fields"] if field["name"] == "app_list")
        for row in rows:
            row["enabled"] = row["app_id"] in results
        call("save_configuration", app_id="_group", values=dict(app_list=rows), revision=description["revision"])
        state = wait(call("prepare_application", app_id="one_dragon")["job_id"])
        assert state["state"] == "prepared" and not state["uses_charge"], state
        assert {item["id"] for item in state["prepared_applications"]} == {"redemption_code", "life_on_line"}, state
        assert len(state["prepared_requirements"]["scripts"]) == 1, state
        probe = call("runtime_probe")
        assert not probe["window_bound"] and not probe["upstream_hotkeys_started"], probe
        print(json.dumps(dict(prepared=results, group=state["prepared_requirements"], probe=probe, game_started=False), ensure_ascii=False), flush=True)
    finally:
        while True:
            try:
                client.close(timeout=5)
                break
            except RequestPending:
                print("Waiting for preparation worker", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
