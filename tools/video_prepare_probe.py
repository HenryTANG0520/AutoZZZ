"""Opt-in AutoZZZ video-store preparation test; never sends Start Business input."""
from __future__ import annotations

import argparse
import ctypes as C
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from autozzz.app import Context, Runner
from autozzz.config import ROOT, Settings
from autozzz.models import AutomationError, Token, normalize
from autozzz.vision import Vision
from autozzz.windows import find_game, kernel32
from autozzz.workflows import DailyWorkflow


class PreparationReady(Exception):
    pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('observe', 'prepare'))
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--stop-after-shelf-entry', action='store_true')
    parser.add_argument('--stop-after-recommendation', action='store_true')
    args = parser.parse_args()
    directory = ROOT / 'runs' / 'video-prepare' / datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    directory.mkdir(parents=True)
    proof = {'status': 'failed', 'action': args.action, 'run_dir': str(directory),
             'start_business_inputs_sent': 0, 'stamina_spent': 0}
    runner = context = mutex = deadline = hotkeys = None
    try:
        settings = Settings.load(args.config)
        settings.auto_launch = False
        settings.scratch = settings.coffee = settings.daily = False
        settings.video = True
        settings.validate()
        kernel32.CreateMutexW.argtypes = [C.c_void_p, C.c_int, C.c_wchar_p]
        kernel32.CreateMutexW.restype = C.c_void_p
        C.set_last_error(0)
        mutex = kernel32.CreateMutexW(None, False, 'Local\\AutoZZZ.DailyRunner')
        if not mutex or C.get_last_error() == 183:
            raise AutomationError('Another AutoZZZ task is running.')
        events = []
        runner = Runner(settings, events.append)
        runner.report.run_dir = str(directory)
        runner.report.server, runner.report.game_path = settings.server, settings.game_path
        now = datetime.now(timezone(timedelta(hours=8)))
        runner.report.started_at = now.isoformat()
        runner.report.reset_day = ((now - timedelta(hours=4)).date().isoformat()
                                   if settings.server == 'cn' else 'unverified-global-reset')
        deadline = threading.Timer(600, runner.stop)
        deadline.daemon = True
        deadline.start()
        runner._window = find_game(settings.game_path)
        runner._prepare_window()
        hotkeys = threading.Thread(target=runner._hotkeys, daemon=True)
        hotkeys.start()
        context = Context(runner, runner._window, Vision(settings.ocr_confidence))
        original_snapshot, original_click = context.snapshot, context.click
        previous_text = None
        observed = []

        def observed_snapshot():
            nonlocal previous_text
            frame = original_snapshot()
            if frame.text != previous_text:
                image = context.evidence(frame, f'frame_{len(observed):02}')
                observed.append({'screenshot': str(image), 'tokens': [t.__dict__ for t in frame.tokens]})
                previous_text = frame.text
            return frame

        def preparation_click(frame, target):
            if isinstance(target, Token) and normalize(target.text) == '开始营业':
                context.evidence(frame, 'ready_to_open')
                proof['status'] = 'opening_intercepted'
                raise PreparationReady('Start Business attempt intercepted; no opening click was sent.')
            original_click(frame, target)
            if (args.stop_after_recommendation and isinstance(target, Token)
                    and normalize(target.text) == '推荐上架'):
                context.sleep(1)
                after = context.snapshot()
                proof.update(status='recommendation_observed', screenshot=str(context.evidence(after, 'recommended_result')))
                raise PreparationReady('Recommendation observed; no business input was sent.')
            if (args.stop_after_shelf_entry and isinstance(target, tuple)
                    and 1100 / 1920 <= target[0] <= 1320 / 1920
                    and 650 / 1080 <= target[1] <= 900 / 1080
                    and workflow._video_business_panel(frame)):
                context.sleep(1)
                after = context.snapshot()
                proof.update(status='shelf_observed', screenshot=str(context.evidence(after, 'shelf_selection')))
                raise PreparationReady('Shelf selection observed; no recommendation or business input sent.')

        context.snapshot, context.click = observed_snapshot, preparation_click
        initial = context.snapshot()
        if args.action == 'prepare':
            workflow = DailyWorkflow(context)
            try:
                result = runner._resume_step(workflow.video)
                proof.update(status=result.status, detail=result.detail)
            except PreparationReady as ready:
                proof['detail'] = str(ready)
        else:
            proof.update(status='observed', screenshot=str(context.evidence(initial, 'observed')))
        proof.update(server=settings.server, promoter=settings.promoter, frames=observed, events=events)
    except Exception as error:
        proof.update(error=str(error))
        if context and context.last_scene:
            try:
                proof['screenshot'] = str(context.evidence(context.last_scene, 'failure'))
            except Exception:
                pass
    finally:
        if deadline:
            deadline.cancel()
        if runner:
            runner.stop()
            runner._monitor_end.set()
        if hotkeys:
            hotkeys.join(timeout=1)
        if mutex:
            kernel32.CloseHandle(mutex)
        (directory / 'proof.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding='utf-8')
        (ROOT / 'runs' / 'video-prepare' / 'latest.json').write_text(
            json.dumps(proof, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if proof['status'] in ('observed', 'opening_intercepted', 'shelf_observed', 'recommendation_observed', 'ok') else 1


if __name__ == '__main__':
    raise SystemExit(main())
