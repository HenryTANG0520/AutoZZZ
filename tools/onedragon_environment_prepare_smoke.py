"""实际独立进程重新准备，核实新 OCR 与截图配置；子进程硬拦截外部操作。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from autozzz.config import GAME_PATHS
from autozzz.onedragon_runtime import OneDragonClient, RuntimePaths
from autozzz.onedragon_session import RuntimeSession


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    args = parser.parse_args()
    marker = args.data / ".autozzz-environment-prepare-test"
    if args.data.exists() and any(args.data.iterdir()) and not marker.is_file(): raise SystemExit("请使用空目录或本工具拥有的测试目录。")
    args.data.mkdir(parents=True, exist_ok=True)
    marker.write_text("isolated process refresh", encoding="utf-8")
    root = Path(__file__).resolve().parents[1]
    defaults = RuntimePaths.discover(root)
    paths = RuntimePaths(defaults.source, defaults.python, args.data.resolve())
    paths.prepare()
    cache = root / "runs/onedragon-20261029/hunt-preparation/assets/models/onnx_ocr/ppocrv6"
    if not cache.is_dir(): raise SystemExit("请提供已有 OCR 缓存；本工具不下载模型。")
    for name in ("ppocrv6", "autozzz-local-ocr-fixture"):
        shutil.copytree(cache, paths.data / "assets/models/onnx_ocr" / name, dirs_exist_ok=True)
    # 只替换测试启动命令；生产 RuntimeSession 和请求/进程退出逻辑保持原样。
    bootstrap = "\n".join([
        "import sys, platform", f"sys.path.insert(0, {str(paths.source / 'src')!r})", "platform.uname()",
        "from contextlib import ExitStack", "from unittest.mock import patch",
        "from autozzz.onedragon_worker import main", "from autozzz.onedragon_host import ManagedController",
        "from zzz_od.operation.enter_game.open_game import OpenGame",
        "def forbidden(*a, **k): raise AssertionError('offline preparation forbids external effects')",
        "with ExitStack() as stack:",
        "    for target in ('subprocess.Popen', 'winreg.SetValueEx', 'winreg.DeleteValue', 'requests.sessions.Session.request', 'socket.socket.connect', 'socket.socket.connect_ex', 'socket.getaddrinfo'):",
        "        stack.enter_context(patch(target, side_effect=forbidden))",
        "    stack.enter_context(patch.object(ManagedController, 'bind', side_effect=forbidden))",
        "    stack.enter_context(patch.object(OpenGame, 'execute', side_effect=forbidden))",
        "    raise SystemExit(main())",
    ])
    original_spawn = subprocess.Popen
    def spawn(command, **kwargs):
        if isinstance(command, list) and command[1:4] == ["-u", "-m", "autozzz.onedragon_worker"]:
            command = [command[0], "-u", "-c", bootstrap, *command[4:]]
        return original_spawn(command, **kwargs)
    clients, events, logs = [], queue.Queue(), []
    class ObservedClient(OneDragonClient):
        def start(self, *a, **k):
            assert all(not old.alive and old.worker_exit_code == 0 for old in clients)
            result = super().start(*a, **k)
            clients.append(self)
            return result
    def wait_event(predicate, timeout=90):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            event = events.get(timeout=5)
            if event["type"] == "log": logs.append(event["message"])
            if event["type"] == "error": raise AssertionError(event["message"] + "\n" + "\n".join(logs[-15:]))
            if predicate(event): return event
        raise AssertionError("原独立进程观察超时")
    with patch("autozzz.onedragon_session.OneDragonClient", ObservedClient), patch("autozzz.onedragon_runtime.subprocess.Popen", side_effect=spawn):
        session = RuntimeSession(paths, events.put, report_root=paths.data / "reports")
        def rpc(method, **params):
            session.send("rpc", method=method, tag=method, params=params)
            return wait_event(lambda e: e["type"] == "rpc_result" and e["tag"] == method)["result"]
        def save(app_id, **values):
            description = rpc("describe_configuration", app_id=app_id)
            return rpc("save_configuration", app_id=app_id, values=values, revision=description["revision"])
        try:
            wait_event(lambda e: e["type"] == "connected")
            save("_environment", proxy_type="None", screenshot_method="bitblt", is_debug=False)
            save("_models", ocr="ppocrv6", ocr_use_gpu=False)
            params = dict(app_id="email", server="global", game_path=GAME_PATHS["global"], region="asia")
            session.send("prepare", **params)
            wait_event(lambda e: e["type"] == "state" and e["state"]["state"] == "prepared")
            first = rpc("runtime_probe")
            assert first["ocr_model"] == "ppocrv6" and first["screenshot_method"] == "bitblt", first
            assert not first["window_bound"] and not first["upstream_hotkeys_started"] and not first["game_input_sent"]
            save("_models", ocr="autozzz-local-ocr-fixture")
            save("_environment", screenshot_method="pil")
            # 直接重复初始化被拒绝，不能在旧识别器上假装切换完成。
            for method, request in (("initialize_execution", dict(server="global", game_path=GAME_PATHS["global"], region="asia")),
                                    ("prepare_application", dict(app_id="email")), ("start_application", dict(app_id="email"))):
                session.send("rpc", method=method, tag="stale", params=request)
                deadline = time.monotonic() + 10
                rejected = False
                while time.monotonic() < deadline:
                    event = events.get(timeout=5)
                    if event["type"] == "error":
                        assert "重新连接" in event["message"]
                        rejected = True; break
                assert rejected, method
            session.send("prepare", **params)
            wait_event(lambda e: e["type"] == "state" and e["state"]["state"] == "prepared")
            second = rpc("runtime_probe")
            assert second["ocr_model"] == "autozzz-local-ocr-fixture" and second["screenshot_method"] == "pil", second
            assert "400" in first["ocr_text"] and "400" in second["ocr_text"]
            assert not second["window_bound"] and not second["upstream_hotkeys_started"] and not second["game_input_sent"]
            assert len(clients) == 2 and clients[0].worker_pid != clients[1].worker_pid and not clients[0].alive
        finally:
            session.close()
            session.worker.join(45)
            assert not session.worker.is_alive(), "必须等待同一进程退出"
            (paths.data / "preparation.log").write_text("\n".join(logs), encoding="utf-8")
        assert all(not c.alive and c.worker_exit_code == 0 for c in clients)
        assert list((paths.data / "reports").rglob("report.json")) == []
    print(json.dumps(dict(workers=[c.worker_pid for c in clients], first=first, second=second, actual_process_refresh=True,
                         stale_initialization_rejected=True, external_operations_blocked=True, game_input_sent=False), ensure_ascii=False))


if __name__ == "__main__": main()
