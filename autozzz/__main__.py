"""GUI entry point and command-line diagnostics for AutoZZZ."""
from __future__ import annotations

import argparse
import json
import signal
import sys
from pathlib import Path
from typing import Any

from .config import CONFIG_PATH, ROOT, Settings


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        description="AutoZZZ：通过屏幕识别完成绝区零非战斗每日任务。无模式参数时打开中文图形界面。"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="检测游戏窗口和 OCR，输出 JSON；不点击游戏")
    mode.add_argument("--launch", action="store_true", help="启动所选区服并自动进入游戏主界面")
    mode.add_argument("--run", action="store_true", help="按配置执行每日；F9 暂停，F10 停止")
    mode.add_argument("--self-test", action="store_true", help="运行源码版离线测试，不访问游戏")
    parser.add_argument("--server", choices=("cn", "global"), help="cn 国服，global 国际服；自动选择默认路径")
    parser.add_argument("--game-path", help="覆盖所选区服的 ZenlessZoneZero.exe 路径")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH, help="配置 JSON 路径")
    args = parser.parse_args(argv)

    if args.self_test:
        import unittest

        test_path = ROOT / "tests"
        if getattr(sys, "frozen", False) or not test_path.is_dir():
            print(_json({"error": "请在源码目录运行 --self-test；打包版不包含离线测试。"}), file=sys.stderr)
            return 2
        tests = unittest.defaultTestLoader.discover(str(test_path), pattern="test_*.py")
        if tests.countTestCases() == 0:
            print(_json({"error": "未找到离线测试。"}), file=sys.stderr)
            return 2
        result = unittest.TextTestRunner(verbosity=2).run(tests)
        return 0 if result.wasSuccessful() else 1

    if not any((args.check, args.launch, args.run)):
        from .gui import main as gui_main

        gui_main(args.config, server=args.server, game_path=args.game_path)
        return 0

    try:
        settings = Settings.load(args.config)
        if args.server:
            settings.select_server(args.server)
        if args.game_path:
            settings.game_path = args.game_path
        settings.validate()
        from .app import Runner, inspect_game

        if args.check:
            print(_json(inspect_game(settings)))
            return 0
        def emit(event: dict[str, Any]) -> None:
            if event.get("type") != "finished":
                print(json.dumps(event, ensure_ascii=False, default=str), file=sys.stderr, flush=True)

        runner = Runner(settings, emit)
        previous_handlers = {}

        def stop_on_signal(_signum: int, _frame: Any) -> None:
            runner.stop()

        # Request a cooperative stop so the backend can persist its final report.
        for signum in (signal.SIGINT, getattr(signal, "SIGBREAK", None)):
            if signum is not None:
                previous_handlers[signum] = signal.signal(signum, stop_on_signal)
        try:
            report = runner.enter_game() if args.launch else runner.run()
        finally:
            for signum, handler in previous_handlers.items():
                signal.signal(signum, handler)
        data = report.to_dict() if hasattr(report, "to_dict") else report
        print(_json(data))
        status = data.get("status") if isinstance(data, dict) else "failed"
        return 0 if status == "success" else (130 if status == "stopped" else 1)
    except Exception as exc:
        print(_json({"error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
