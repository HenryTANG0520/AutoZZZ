"""Offline GUI acceptance checks. All game backend calls are replaced before use."""
from __future__ import annotations

import importlib
import json
import sys
import tempfile
import threading
import time
import tkinter as tk
import types
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PACKAGE = 'autohsr' if (ROOT / 'autohsr').exists() else 'autozzz'
importlib.import_module(PACKAGE + '.windows')  # establish DPI before Tk; no game actions
Settings = importlib.import_module(PACKAGE + '.config').Settings
module = importlib.import_module(PACKAGE + '.gui')
Window = module.AutoHSRWindow if PACKAGE == 'autohsr' else module.AutoZZZWindow
from PIL import ImageGrab

OUT = ROOT / 'runs' / 'ux-smoke'
OUT.mkdir(parents=True, exist_ok=True)
checks = []


def pump(root, predicate=lambda: True, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        root.update()
        if predicate():
            return
        time.sleep(.02)
    raise AssertionError('GUI condition timed out')


def check(name, condition):
    assert condition, name
    checks.append(name)


def layout(root):
    def walk(widget):
        for child in widget.winfo_children():
            if not child.winfo_ismapped():
                continue
            x, y = child.winfo_rootx() - root.winfo_rootx(), child.winfo_rooty() - root.winfo_rooty()
            assert x >= 0 and y >= 0, (str(child), x, y)
            assert x + child.winfo_width() <= root.winfo_width() + 2, (str(child), 'width')
            assert y + child.winfo_height() <= root.winfo_height() + 2, (str(child), 'height')
            if child.winfo_class() in ('TLabel', 'TButton', 'TCheckbutton', 'TEntry', 'TCombobox'):
                assert child.winfo_height() >= child.winfo_reqheight(), (str(child), 'clipped')
            walk(child)
    walk(root)


class FakeRunner:
    instances = []
    def __init__(self, settings, emit, **kwargs):
        self.settings, self.emit, self.kwargs = settings, emit, kwargs
        self.release = threading.Event()
        self.paused = False
        self.stopped = False
        self.report = types.SimpleNamespace(operation='daily')
        self._window = None
        self.instances.append(self)

    def run(self):
        task = 'battle' if PACKAGE == 'autohsr' else 'scratch'
        self.emit(dict(type='task', task=task, status='running', detail='离线假后端，无游戏输入'))
        self.release.wait(5)
        report = dict(status='stopped' if self.stopped else 'success', stamina_spent=0,
                      daily_verified=False if self.stopped else True, engagement=400,
                      operation=self.kwargs.get('operation', self.report.operation), results=[])
        self.emit(dict(type='finished', report=report))
        return report

    def enter_game(self):
        report = dict(status='success', operation='enter_game', results=[dict(task='launch', status='ok', detail='模拟进入')])
        self.emit(dict(type='finished', report=report))
        return report

    def toggle_pause(self):
        self.paused = not self.paused
        self.emit(dict(type='paused', paused=self.paused))
        return self.paused

    def stop(self):
        self.stopped = True
        self.release.set()


def main():
    fake = types.ModuleType(PACKAGE + '.app')
    fake.Runner = FakeRunner
    fake.inspect_game = lambda _: dict(title='离线模拟窗口', width=1920, height=1080,
        administrator=True, input_ready=True, ocr_text='离线假后端，未启动游戏')
    fake.launch_game = lambda _: '离线模拟启动，未访问游戏'
    errors = []
    with tempfile.TemporaryDirectory() as temp, patch.dict(sys.modules, {PACKAGE + '.app': fake}), \
            patch.object(module.messagebox, 'showerror', side_effect=lambda *a, **k: errors.append(a)), \
            patch.object(module.messagebox, 'showinfo'), patch.object(module.messagebox, 'askyesno', return_value=False):
        config = Path(temp) / 'config.json'
        Settings().save(config)
        original = config.read_bytes()
        root = tk.Tk()
        def callback_error(kind, value, traceback):
            raise value.with_traceback(traceback)
        root.report_callback_exception = callback_error
        ui = Window(root, config)
        try:
            pump(root)
            ui._log('离线 UX 验收：此界面使用假后端，未运行游戏。')
            for key in ui.pages:
                ui._show_page(key)
                pump(root)
                layout(root)
                if key=='applications':
                    check('application_has_its_own_actions',not ui.footer.winfo_ismapped())
                    ui.busy=True
                    ui._refresh_controls()
                    pump(root)
                    check('running_legacy_task_keeps_emergency_controls',all(w.winfo_ismapped() for w in (ui.pause_button,ui.stop_button)))
                    ui.busy=False
                    ui._refresh_controls()
                else:
                    check('persistent_controls_' + key, all(w.winfo_ismapped() for w in (ui.pause_button,ui.stop_button)))
                    check('daily_actions_scoped_' + key, bool(ui.start_button.winfo_ismapped())==(key!='combat'))
                ImageGrab.grab(window=root.winfo_id()).save(OUT / ('page-' + key + '.png'))
            ui._show_page('home')
            for value in ui.tasks.values():
                value.set(False)
            if PACKAGE == 'autohsr':
                ui.stamina_limit.set('invalid disabled draft')
                ui.target_name.set('')
            else:
                ui.promoter.set('invalid ignored draft')
            ui._check()
            pump(root, lambda: not ui.busy)
            check('detect_independent_of_tasks_and_irrelevant_draft', not errors)
            check('detect_does_not_save', config.read_bytes() == original)
            ui._launch()
            pump(root, lambda: not ui.busy)
            check('launch_does_not_save_or_require_daily', config.read_bytes() == original and not errors)
            if PACKAGE == 'autohsr':
                # The resource draft is deliberately invalid; reward-only must ignore it.
                ui._reward_only()
                pump(root, lambda: ui.runner is not None)
                FakeRunner.instances[-1].release.set()
                pump(root, lambda: not ui.busy)
                check('reward_only_ignores_irrelevant_invalid_battle_draft', not errors and config.read_bytes() == original)
            # Restore a valid selected draft. Starting it must not save.
            for key, value in ui.tasks.items():
                value.set(key not in ('mail', 'battlepass'))
            if PACKAGE == 'autohsr':
                ui.stamina_limit.set('10')
                ui.rounds.set('1')
                ui.target_name.set('回忆之蕾')
            else:
                ui.promoter.set('')
            ui.auto_launch.set(not ui.settings.auto_launch)
            if PACKAGE == 'autohsr':
                before_count = len(FakeRunner.instances)
                with patch('autohsr.recovery.recovery_state', return_value=dict(blocked=True, corrupt=False, spent=210, pending=10)):
                    ui._start()
                check('declining_new_budget_does_not_start_or_save', len(FakeRunner.instances) == before_count and config.read_bytes() == original)
            ui._start()
            pump(root, lambda: ui.runner is not None)
            check('start_does_not_save', config.read_bytes() == original)
            check('busy_locks_settings', all(str(w.cget('state')) == 'disabled' for w in ui.setting_controls))
            ui._pause()
            pump(root, lambda: ui.paused)
            check('pause_preserves_control', str(ui.stop_button.cget('state')) == 'normal')
            ui.emit(dict(type='focus_wait', waiting=True))
            pump(root, lambda: ui.events.empty())
            check('manual_pause_is_not_overridden_by_focus_wait', ui.paused)
            ui._stop()
            pump(root, lambda: not ui.busy)
            check('stop_does_not_save', config.read_bytes() == original)
            ui._show_page('applications')
            pump(root)
            check('legacy_result_kept_after_stopping_from_another_page',ui.footer.winfo_ismapped() and not ui.start_button.winfo_ismapped())
            ui._show_page('home')
            # Reward-only snapshot: do not persist or restart a battle.
            ui._reward_only()
            pump(root, lambda: ui.runner is not None)
            runner = FakeRunner.instances[-1]
            check('active_plan_identifies_reward_only_snapshot', '仅补领奖' in ui.plan.get())
            check('reward_only_has_no_battle_or_regular_actions',
                not runner.settings.battle if PACKAGE == 'autohsr' else not any((runner.settings.scratch, runner.settings.coffee, runner.settings.video)))
            runner.release.set()
            pump(root, lambda: not ui.busy)
            check('reward_only_does_not_change_saved_plan', config.read_bytes() == original)
            # Failure presentation retains both core proof and the optional cause.
            report = dict(status='partial', daily_verified=PACKAGE == 'autohsr', engagement=200,
                stamina_spent=210 if PACKAGE == 'autohsr' else 0, stamina_limit=240, stamina_pending=10,
                error='模拟邮件入口未识别', safe_next='人工返回主界面后仅补领奖', results=[
                    dict(task='mail' if PACKAGE == 'autohsr' else 'video', status='partial', detail='模拟入口未识别'),
                    dict(task='close_game', status='skipped', detail='本轮未完整核实')])
            (ui._finish if PACKAGE == 'autohsr' else ui._finished)(report)
            check('failure_cause_next_action_and_exit_are_visible', all(x in ui.summary.get() for x in ('原因', '下一步', '游戏保留')))
            ui._show_page('home')
            pump(root)
            layout(root)
            ImageGrab.grab(window=root.winfo_id()).save(OUT / 'failure-recovery.png')
            # A long diagnostic can be stopped cooperatively; discard its result.
            started, release = threading.Event(), threading.Event()
            def inspect(_):
                started.set()
                release.wait(4)
                return dict(title='cancelled mock', width=1920, height=1080)
            fake.inspect_game = inspect
            ui._check()
            pump(root, lambda: started.is_set())
            ui._stop()
            release.set()
            pump(root, lambda: not ui.busy)
            check('diagnostic_stop_discards_result', '停止' in ui.status.get())
            check('failed_or_cancelled_diagnostic_does_not_save', config.read_bytes() == original)
            # Explicit save and dirty-close cancel/discard.
            ui._save_settings()
            check('explicit_save_persists', config.read_bytes() != original and not errors)
            saved = config.read_bytes()
            ui.auto_launch.set(not ui.auto_launch.get())
            with patch.object(module.messagebox, 'askyesnocancel', return_value=None):
                ui._close()
            check('dirty_close_cancel_keeps_window_and_file', not ui.closing and config.read_bytes() == saved)
            root.geometry('1100x800')
            for key in ui.pages:
                ui._show_page(key)
                pump(root)
                layout(root)
            check('minimum_layout_all_pages', True)
            with patch.object(module.messagebox, 'askyesnocancel', return_value=False):
                ui._close()
            check('dirty_close_discard_does_not_save', config.read_bytes() == saved)
        finally:
            if getattr(ui, 'runner', None):
                ui.runner.stop()
            if getattr(ui, 'worker', None):
                ui.worker.join(2)
            try:
                for token in root.tk.splitlist(root.tk.call('after', 'info')):
                    root.after_cancel(token)
                if getattr(ui, 'overlay', None):
                    ui.overlay.close()
                root.destroy()
            except tk.TclError:
                pass
    result = dict(product=PACKAGE, mock_backend=True, game_started=False, checks=checks, count=len(checks))
    (OUT / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
