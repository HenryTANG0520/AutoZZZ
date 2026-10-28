"""初始化真实上游 OCR 与受控控制器；合成文字识别不访问游戏。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from autozzz.config import GAME_PATHS
from autozzz.onedragon_runtime import OneDragonClient, RuntimePaths, RemoteError, RequestPending


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", choices=("cn", "global"), default="cn")
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    default = RuntimePaths.discover(root, args.server)
    client = OneDragonClient(RuntimePaths(default.source, default.python, args.data),
                            log=lambda line: print(line, file=sys.stderr, flush=True))
    try:
        hello = client.start()
        assert len(client.call("catalog")["applications"]) == 30
        initial = client.call("initialize_execution", server=args.server, game_path=GAME_PATHS[args.server])
        job = initial["job_id"]
        print(json.dumps(dict(worker_pid=client.worker_pid, launcher_pid=client.process.pid,
                              initialization_job=job)), file=sys.stderr, flush=True)
        deadline = time.monotonic() + args.timeout
        stop_requested = False
        while True:
            state = client.call("execution_status", timeout=5)
            assert state["job_id"] == job, "查询必须对应同一初始化任务"
            if not state["busy"]:
                break
            if time.monotonic() >= deadline and not stop_requested:
                # 观察超时仅请求停止，继续等待同一任务和进程退出。
                print(json.dumps(client.call("stop_execution"), ensure_ascii=False), file=sys.stderr)
                stop_requested = True
            time.sleep(.2)
        assert state["state"] == "ready", state
        assert len(client.call("catalog")["applications"]) == 30
        probe = client.call("runtime_probe", timeout=30)
        assert "400" in probe["ocr_text"], probe
        assert not probe["window_bound"] and not probe["upstream_hotkeys_started"], probe
        try:
            client.call("start_application", app_id="charge_plan")
            raise AssertionError("预算与界面未接入前不能开放游戏执行请求")
        except RemoteError:
            pass
        print(json.dumps(dict(hello=hello, initialization=state, probe=probe), ensure_ascii=False))
    finally:
        while True:
            try:
                client.close(timeout=5)
                break
            except RequestPending:
                print(f"Waiting for original worker PID {client.worker_pid} to exit", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
