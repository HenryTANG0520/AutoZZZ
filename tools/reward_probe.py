"""Explicit, bounded AutoZZZ reward calibration; no arbitrary desktop inputs."""
from __future__ import annotations

import argparse
import ctypes as C
from datetime import datetime
import json
from pathlib import Path
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from autozzz.app import Context, Runner
from autozzz.config import ROOT, Settings
from autozzz.models import AutomationError
from autozzz.vision import Vision
from autozzz.windows import find_game, kernel32
from autozzz.workflows import DailyWorkflow


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('enter', 'observe', 'weekly', 'city_fund', 'both'))
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    directory = ROOT / 'runs' / 'reward-probe' / datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    directory.mkdir(parents=True)
    proof = dict(status='failed', action=args.action, run_dir=str(directory), stamina_spent=0)
    runner = context = mutex = timer = hotkeys = None
    events = []
    frames = []
    try:
        settings = Settings.load(args.config)
        runner = Runner(settings, events.append)
        runner.report.run_dir = str(directory)
        kernel32.CreateMutexW.argtypes = [C.c_void_p, C.c_int, C.c_wchar_p]
        kernel32.CreateMutexW.restype = C.c_void_p
        C.set_last_error(0)
        mutex = kernel32.CreateMutexW(None, False, 'Local\\AutoZZZ.DailyRunner')
        if not mutex or C.get_last_error() == 183:
            raise AutomationError('Another AutoZZZ task is running.')
        timer = threading.Timer(600, runner.stop)
        timer.daemon = True
        timer.start()
        runner._window = find_game(settings.game_path)
        runner._prepare_window()
        hotkeys = threading.Thread(target=runner._hotkeys, daemon=True)
        hotkeys.start()
        context = Context(runner, runner._window, Vision(settings.ocr_confidence))
        snapshot = context.snapshot
        last_text = None

        def observe():
            nonlocal last_text
            frame = snapshot()
            # Calibration never submits an authentication panel.
            if DailyWorkflow(context)._cached_login(frame):
                raise AutomationError('请手动完成已保存账号登录，再继续奖励校准。')
            if frame.text != last_text:
                path = context.evidence(frame, f'frame_{len(frames):02}')
                frames.append(dict(screenshot=str(path), tokens=[t.__dict__ for t in frame.tokens]))
                last_text = frame.text
            return frame

        context.snapshot = observe
        initial = runner._initial_snapshot(context)
        if args.action == 'observe':
            proof.update(status='observed', screenshot=str(context.evidence(initial, 'observed')))
        else:
            workflow = DailyWorkflow(context)
            result = runner._resume_step(workflow.ensure_main)
            if args.action == 'enter':
                proof.update(status=result.status, detail=result.detail)
            else:
                selected = ('weekly', 'city_fund') if args.action == 'both' else (args.action,)
                results = []
                for name in selected:
                    workflow = runner._reward_workflow(name, context)
                    result = runner._resume_step(getattr(workflow, name))
                    results.append(result.__dict__)
                    if result.status != 'ok':
                        break
                proof.update(status='ok' if all(r['status'] == 'ok' for r in results) else 'partial', results=results)
        proof.update(server=settings.server, events=events, frames=frames)
    except Exception as error:
        proof.update(error=str(error), events=events)
        if context and context.last_scene:
            try:
                proof['screenshot'] = str(context.evidence(context.last_scene, 'failure'))
            except Exception:
                pass
    finally:
        proof['frames'] = frames
        if timer:
            timer.cancel()
        if runner:
            runner.stop()
            runner._monitor_end.set()
        if hotkeys:
            hotkeys.join(timeout=1)
        if mutex:
            kernel32.CloseHandle(mutex)
        (directory / 'proof.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding='utf-8')
        (directory.parent / 'latest.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if proof['status'] in ('ok', 'observed') else 1


if __name__ == '__main__':
    raise SystemExit(main())
