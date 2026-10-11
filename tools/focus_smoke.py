"""Opt-in live focus test: own test window + game captures; no game clicks or keys."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
import threading
import time
import tkinter as tk

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from autozzz.app import Context, Runner
from autozzz.config import ROOT, Settings
from autozzz.models import FocusLost
from autozzz.vision import Vision
from autozzz.windows import find_game, is_admin, is_elevated, user32


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server', choices=('cn', 'global'), default='cn')
    args = parser.parse_args()
    folder = ROOT / 'runs' / 'focus-smoke' / datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    folder.mkdir(parents=True)
    events = []
    proof = {'status': 'failed', 'server': args.server, 'game_inputs_sent': 0}
    runner = None
    root = None
    worker = None
    outcome = {}
    restored_at = [None]
    try:
        settings = Settings(scratch=False, coffee=False, video=False, daily=True)
        settings.select_server(args.server)
        window = find_game(settings.game_path)
        if is_elevated(window.hwnd) and not is_admin():
            raise RuntimeError('Run this opt-in game test as administrator.')
        vision = Vision(settings.ocr_confidence)
        runner = Runner(settings, lambda event: events.append(dict(event, observed_at=time.monotonic())))
        runner._window = window
        runner.report.run_dir = str(folder)
        runner._prepare_window()
        context = Context(runner, window, vision)

        def forbid_game_input(*_args, **_kwargs):
            raise AssertionError('The live focus probe must never send game input.')

        for name in ('click', 'key', 'drag', 'scroll', 'move', 'send'):
            setattr(window, name, forbid_game_input)

        root = tk.Tk()
        root.title('AutoZZZ focus test')
        root.geometry('440x150')
        tk.Label(root, text='AutoZZZ focus test\nThe helper will wait, then restore the game.\nNo game clicks or keys are sent.',
                 padx=16, pady=20).pack()
        root.update()
        root.lift()
        root.focus_force()
        root.update()
        if user32.GetForegroundWindow() == window.hwnd:
            # Some fullscreen clients retain foreground against a Tk activation.
            # Minimize the observed client to exercise the same cancellable wait.
            user32.ShowWindow(window.hwnd, 6)
            root.update()
            proof['loss_mode'] = 'minimized'
        else:
            proof['loss_mode'] = 'foreground'
        if user32.GetForegroundWindow() == window.hwnd and not user32.IsIconic(window.hwnd):
            raise AssertionError('Focus loss was not observed; the probe cannot claim a pass.')

        def work():
            try:
                runner.checkpoint()
                if restored_at[0] is None:
                    raise AssertionError('Focus wait returned before the controlled restore.')
                frame = context.snapshot()
                assert frame.captured_at >= restored_at[0], 'A pre-wait capture was reused.'
                assert frame.geometry == window.guard(refresh=True)
                context.evidence(frame, 'focus_restored')
                outcome.update(fresh_capture=True, focus_epoch=runner.focus_epoch,
                               input_epoch=runner.input_epoch, inactive_seconds=runner.inactive_duration)
            except Exception as error:
                outcome['error'] = str(error)
            finally:
                outcome['done'] = True

        worker = threading.Thread(target=work, name='AutoZZZ-focus-probe')
        worker.start()
        deadline = time.monotonic() + 45
        restore_scheduled = [False]

        def restore_game():
            if outcome.get('done'):
                return
            restored_at[0] = time.monotonic()
            root.iconify()
            try:
                window.focus()
            except FocusLost:
                # A fullscreen client can briefly report a zero-sized client
                # after restoration; the runner keeps waiting for valid pixels.
                pass
            except Exception as error:
                outcome['restore_error'] = str(error)
                runner.stop()

        def poll():
            if outcome.get('done'):
                root.quit()
                return
            if time.monotonic() >= deadline:
                outcome['deadline_expired'] = True
                runner.stop()
            elif runner._focus_waiting and not restore_scheduled[0]:
                restore_scheduled[0] = True
                root.after(2000, restore_game)
            root.after(50, poll)

        def cancel():
            runner.stop()
            root.withdraw()

        root.protocol('WM_DELETE_WINDOW', cancel)
        root.after(50, poll)
        root.mainloop()
        worker.join(timeout=5)
        assert not worker.is_alive(), 'The probe worker did not finish cooperatively.'
        assert not outcome.get('error'), outcome.get('error')
        assert not outcome.get('restore_error'), outcome.get('restore_error')
        assert not outcome.get('deadline_expired'), 'The controlled focus restore timed out.'
        waiting = [event['waiting'] for event in events if event.get('type') == 'focus_wait']
        assert waiting == [True, False], waiting
        assert outcome['inactive_seconds'] >= 1.5, outcome
        assert not runner._stop.is_set(), 'Focus loss stopped the runner.'
        proof.update(status='passed', focus_events=waiting, **outcome)
    except Exception as error:
        proof['error'] = str(error)
    finally:
        if runner:
            runner.stop()
        if worker and worker.is_alive():
            worker.join(timeout=5)
        if root:
            root.destroy()
        proof['events'] = events
        proof['run_dir'] = str(folder)
        (folder / 'proof.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding='utf-8')
        (ROOT / 'runs' / 'focus-smoke' / 'latest.json').write_text(
            json.dumps(proof, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(proof, ensure_ascii=False, indent=2))
    return 0 if proof['status'] == 'passed' else 1


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
