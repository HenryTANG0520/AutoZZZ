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
        description="AutoZZZ：通过屏幕识别完成绝区零每日任务及独立的预输入战斗。无模式参数时打开中文图形界面。"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="检测游戏窗口和 OCR，输出 JSON；不点击游戏")
    mode.add_argument("--launch", action="store_true", help="启动所选区服并自动进入游戏主界面")
    mode.add_argument("--run", action="store_true", help="按配置执行每日；F9 暂停，F10 停止")
    mode.add_argument("--battle-catalog", action="store_true", help="读取训练目录，不出战")
    mode.add_argument("--battle", action="store_true", help="按独立方案及电量预算执行战斗批次")
    mode.add_argument("--battle-resume", type=Path, help="仅续接报告对应的未完成战斗；不重新出战")
    mode.add_argument("--battle-prepare", action="store_true", help="核实副本与预编队伍；不出战")
    mode.add_argument("--onedragon-run", metavar="APP_ID", help="运行已保存的一条龙应用配置")
    mode.add_argument("--runtime-check", type=Path, metavar="REPORT", help="检查一条龙分发环境并保存离线报告，不操作游戏")
    mode.add_argument('--program-update',type=Path,metavar='ZIP',help='验证完整更新包，关闭当前进程后安装')
    parser.add_argument('--update-report',type=Path,help='保存程序更新交接报告；报告已存在时不会重复更新')
    parser.add_argument('--no-restart',action='store_true',help='程序更新完成后不重新打开界面')
    parser.add_argument("--energy-limit", type=int, help="一条龙本轮总电量上限，正整数")
    parser.add_argument("--battle-limit", type=int, help="一条龙本轮执行次数上限，正整数")
    parser.add_argument("--battle-cost-limit", type=int, help="一条龙单次电量上限，20—100")
    parser.add_argument("--budget-id", help="沿用同一电量预算编号；存在待核对出战时仍禁止重开")
    parser.add_argument("--account-region", choices=("cn", "cn_b", "us", "eu", "asia", "twhkmo"), help="一条龙账号时区")
    parser.add_argument("--run-timeout", type=int, default=600, help="一条龙运行时限，不小于 30 的整数秒")
    parser.add_argument("--no-auto-launch", action="store_true", help="一条龙直接使用已进入游戏的窗口")
    parser.add_argument('--confirm-resource-actions',action='store_true',help='允许本轮准备清单中的资源操作及第三方插件；操作与消费由配置决定')
    parser.add_argument("--battle-plan", type=Path, default=ROOT / "battle.json", help="独立战斗配置路径")
    parser.add_argument("--battle-strategy", help="覆盖方案中的策略JSON路径，或builtin:策略名")
    mode.add_argument("--self-test", action="store_true", help="运行源码版离线测试，不访问游戏")
    parser.add_argument("--server", choices=("cn", "global"), help="cn 国服，global 国际服；自动选择默认路径")
    parser.add_argument("--game-path", help="覆盖所选区服的 ZenlessZoneZero.exe 路径")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH, help="配置 JSON 路径")
    args = parser.parse_args(argv)
    native_options = (args.energy_limit, args.battle_limit, args.battle_cost_limit, args.budget_id, args.account_region)
    if not args.onedragon_run and (any(value is not None for value in native_options) or args.no_auto_launch or args.confirm_resource_actions or args.run_timeout != 600):
        parser.error("一条龙预算和运行选项只能与 --onedragon-run 一起使用。")
    if args.program_update:
        if args.update_report is None:parser.error('--program-update 需要指定 --update-report。')
        try:
            from .program_update_cli import run_update
            print(_json(run_update(args.program_update,args.update_report,server=args.server or 'cn',restart=not args.no_restart)))
            return 0
        except Exception as exc:
            print(_json(dict(error=str(exc))),file=sys.stderr);return 2
    if args.update_report is not None or args.no_restart:parser.error('程序更新选项只能与 --program-update 一起使用。')

    if args.runtime_check:
        try:
            from .onedragon_diagnostics import check_runtime
            result=check_runtime(ROOT,args.runtime_check)
            print(_json(result))
            return 0 if result['state']=='success' else 2
        except Exception as exc:
            print(_json({'error':str(exc)}),file=sys.stderr)
            return 2

    if args.onedragon_run:
        try:
            from .onedragon_cli import run_application
            from .config import GAME_PATHS
            if args.server is None:
                raise ValueError("一条龙命令行运行须明确指定 --server cn 或 global。")
            result = run_application(args.onedragon_run, server=args.server,
                game_path=args.game_path or GAME_PATHS[args.server],
                region=args.account_region or ("cn" if args.server == "cn" else "asia"),
                allowance=args.energy_limit, battles=args.battle_limit, maximum=args.battle_cost_limit,
                budget_id=args.budget_id,
                timeout=args.run_timeout, auto_launch=not args.no_auto_launch,confirm_resources=args.confirm_resource_actions,
                emit=lambda event: print(json.dumps(event, ensure_ascii=False, default=str), file=sys.stderr, flush=True))
            print(_json(result))
            return {"success": 0, "stopped": 130}.get(result["state"]["state"], 1)
        except Exception as exc:
            print(_json({"error": str(exc)}), file=sys.stderr)
            return 2

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

    if not any((args.check, args.launch, args.run, args.battle, args.battle_prepare, args.battle_resume, args.battle_catalog)):
        from .gui import main as gui_main

        gui_main(args.config, server=args.server, game_path=args.game_path)
        return 0

    try:
        settings = Settings.load(args.config)
        if args.server:
            settings.select_server(args.server)
        if args.game_path:
            settings.game_path = args.game_path
        settings.validate("check" if args.check else "enter_game" if args.launch else "battle" if (args.battle or args.battle_prepare or args.battle_resume or args.battle_catalog) else "daily")
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
            if args.battle or args.battle_prepare or args.battle_resume or args.battle_catalog:
                from .battle import BattlePlan
                plan = BattlePlan.load(args.battle_plan)
                if args.battle_strategy:plan.strategy_file = args.battle_strategy
                report = runner.run(battle_plan=plan, prepare_only=args.battle_prepare, resume_report=args.battle_resume, catalog_only=args.battle_catalog)
            else:
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
