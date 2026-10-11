"""Elevated calibration with existing bounded commands and a 45-minute deadline.

Requests/evidence remain under runs/live-session; no shell or network execution.
F10 and the deadline cancel active actions, including a paused Runner.
"""
from __future__ import annotations

import ctypes as C
import importlib
import json
import math
import os
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from autozzz.app import Context, Runner
from autozzz.config import ROOT, Settings
from autozzz.models import AutomationError, Stopped, TaskResult
from autozzz.vision import Vision
from autozzz.windows import find_game, kernel32, user32
import autozzz.workflows as workflows

SESSION_SECONDS = 45 * 60
ACTIONS = frozenset(('observe', 'handbook', 'scratch', 'coffee', 'video', 'daily', 'main', 'click', 'map', 'quit'))
TASK_ACTIONS = frozenset(('scratch', 'coffee', 'video', 'daily'))
CLICK_LABELS = frozenset(('日常', '前往', '快捷导航', '确认'))
CHINA = timezone(timedelta(hours=8))


def timestamp() -> datetime:
    return datetime.now(CHINA)


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


class LiveSession:
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.session_id = uuid.uuid4().hex
        self.started = timestamp()
        self.deadline = time.monotonic() + SESSION_SECONDS
        self.cancelled = threading.Event()
        self.runner_lock = threading.Lock()
        self.runner: Runner | None = None
        self.reason = ''
        self.seen: set[str] = set()
        # A restart must not replay the old session's last task/quit request.
        try:
            self.last_payload: str | None = (self.directory / 'request.json').read_text(encoding='utf-8-sig')
        except (OSError, UnicodeError):
            self.last_payload = None
        self.completed_requests = 0

    def cancel(self, reason: str) -> None:
        with self.runner_lock:
            if not self.cancelled.is_set():
                self.reason = reason
                self.cancelled.set()
            if self.runner is not None:
                self.runner.stop()  # Clears pause, waking blocked checkpoints.

    def checkpoint(self) -> None:
        if time.monotonic() >= self.deadline:
            self.cancel('deadline')
        if self.cancelled.is_set():
            raise Stopped(f'校准会话已结束：{self.reason}。')

    def watchdog(self) -> None:
        while not self.cancelled.is_set():
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                self.cancel('deadline')
                return
            try:
                if user32.GetAsyncKeyState(0x79) & 0x8000:
                    self.cancel('F10')
                    return
            except Exception as error:
                print(f'F10 monitor failed: {error}', flush=True)
                self.cancel('hotkey-monitor-error')
                return
            self.cancelled.wait(min(0.05, remaining))

    def attach_runner(self, runner: Runner) -> None:
        with self.runner_lock:
            self.runner = runner
            if self.cancelled.is_set() or time.monotonic() >= self.deadline:
                if not self.cancelled.is_set():
                    self.reason = 'deadline'
                    self.cancelled.set()
                runner.stop()

    def detach_runner(self, runner: Runner) -> None:
        with self.runner_lock:
            if self.runner is runner:
                self.runner = None

    @staticmethod
    def validate_request(data) -> tuple[str, str, Settings]:
        if not isinstance(data, dict):
            raise AutomationError('校准请求必须是 JSON 对象。')
        identifier = data.get('id')
        if not isinstance(identifier, str) or not identifier.strip():
            raise AutomationError('校准请求必须包含非空字符串 id。')
        action = data.get('action')
        if not isinstance(action, str) or action not in ACTIONS:
            raise AutomationError('不支持的校准命令。')
        settings = Settings()
        settings.select_server(data.get('server', 'cn'))
        settings.validate()
        if action == 'click':
            label = data.get('label')
            area = data.get('roi', (0, 0, 1, 1))
            if not isinstance(label, str) or not workflows.normalize(label):
                raise AutomationError('点击命令必须指定非空识别文字。')
            if workflows.normalize(label) not in CLICK_LABELS:
                raise AutomationError('该文字不属于校准点击白名单。')
            if not isinstance(area, (list, tuple)) or len(area) != 4 or any(
                    type(value) not in (int, float) or not math.isfinite(value) for value in area):
                raise AutomationError('ROI 必须是四个有限数值。')
            x1, y1, x2, y2 = area
            if not 0 <= x1 < x2 <= 1 or not 0 <= y1 < y2 <= 1:
                raise AutomationError('ROI 必须是客户区内的归一化矩形。')
        return identifier, action, settings

    @staticmethod
    def guard_click_page(workflow, scene, token) -> None:
        if not workflow._known(scene):
            raise AutomationError('未知页面禁止校准点击。')
        label = workflows.normalize(token.text)
        if label == '日常':
            permitted = workflow._handbook(scene) and token.within(workflows.TABS)
        elif label == '快捷导航':
            permitted = workflow._map(scene) and token.within(workflows.roi(1450, 0, 1850, 100))
        elif label == '前往':
            columns = [workflow._daily_column(scene, labels) for labels in workflows.TASK_LABELS.values()]
            permitted = workflow._handbook(scene) and any(
                column and token.within((column[0], 820 / 1080, column[2], 930 / 1080)) for column in columns)
        elif label == '确认':
            # A generic reward/confirmation popup cannot establish a daily action.
            permitted = (workflow._handbook(scene) and scene.has('日常', workflows.TABS, exact=True)
                         and scene.has('活跃度', workflows.DAILY_REWARDS)
                         and scene.has(('获得奖励', '奖励获得', '领取成功'), workflows.DIALOG)
                         and token.within(workflows.roi(850, 670, 1120, 800)))
        else:
            permitted = False
        if not permitted:
            raise AutomationError('点击目标缺少对应非战斗日常页面锚点。')

    def handle_request(self, data, vision) -> bool:
        # Shape and server errors are contained here, keeping the session alive.
        result = {'id': data.get('id', '') if isinstance(data, dict) else ''}
        runner = context = hotkeys = None
        action = 'request'
        should_quit = False
        try:
            identifier, action, settings = self.validate_request(data)
            result['id'] = identifier
            self.checkpoint()
            runner = Runner(settings, lambda event: print(json.dumps(event, ensure_ascii=False), flush=True))
            now = timestamp()
            runner.report.run_dir = str(self.directory / now.strftime('%Y%m%d_%H%M%S_%f'))
            runner.report.server = settings.server
            runner.report.game_path = settings.game_path
            runner.report.started_at = now.isoformat()
            runner.report.reset_day = ((now - timedelta(hours=4)).date().isoformat()
                                       if settings.server == 'cn' else 'unverified-global-reset')
            self.attach_runner(runner)
            runner.checkpoint()
            if action == 'quit':
                runner.report.results.append(TaskResult('quit', 'ok', '已收到匹配的会话退出请求。'))
                runner.report.status = 'success'
                result.update(status='ok', action='quit', session_ended=True)
                self.cancel('quit')
                should_quit = True
            else:
                runner._window = find_game(settings.game_path)
                runner.checkpoint()
                runner._window.focus()
                runner._window.baseline = runner._window.guard()
                hotkeys = threading.Thread(target=runner._hotkeys, name='AutoZZZ-session-hotkeys', daemon=True)
                hotkeys.start()
                context = Context(runner, runner._window, vision)
                importlib.reload(workflows)
                workflow = workflows.DailyWorkflow(context)
                before = context.snapshot()
                context.evidence(before, 'before')
                task_result = None
                if action == 'observe':
                    pass
                elif action == 'handbook':
                    workflow._open_daily()
                elif action in TASK_ACTIONS:
                    task_result = getattr(workflow, action)()
                    runner.report.results.append(task_result)
                    result['task'] = task_result.__dict__
                elif action == 'main':
                    workflow.ensure_main()
                elif action == 'click':
                    label = data['label']
                    area = tuple(data.get('roi', (0, 0, 1, 1)))
                    tokens = [token for token in before.tokens if token.within(area)
                              and workflows.normalize(token.text) == workflows.normalize(label)]
                    if len(tokens) != 1:
                        raise AutomationError('点击目标缺失或存在歧义。')
                    self.guard_click_page(workflow, before, tokens[0])
                    context.click(before, tokens[0])
                    context.sleep(2)
                elif action == 'map':
                    workflow.ensure_main()
                    context.key('m', context.snapshot())
                    context.sleep(2)
                after = context.snapshot()
                result.update(status='ok', screenshot=str(context.evidence(after, 'after')), ocr_text=after.text,
                              tokens=[token.__dict__ for token in after.tokens])
                if task_result is None:
                    runner.report.results.append(TaskResult(action, 'ok', '校准命令已执行并保存前后画面。'))
                runner.report.status = 'success' if all(item.status == 'ok' for item in runner.report.results) else 'partial'
                if action == 'daily' and not runner.report.daily_verified:
                    runner.report.status = 'partial'
        except Stopped as error:
            result.update(status='stopped', error=str(error))
            if runner is not None:
                runner.report.status = 'stopped'
                runner.report.results.append(TaskResult(action, 'stopped', str(error)))
        except Exception as error:
            result.update(status='error', error=str(error))
            if runner is not None:
                runner.report.status = 'failed'
                runner.report.results.append(TaskResult(action, 'failed', str(error)))
        finally:
            if runner is not None:
                runner._monitor_end.set()
                if hotkeys is not None:
                    hotkeys.join(timeout=1)
                if result.get('status') in ('error', 'stopped') and context and context.last_scene:
                    try:
                        result['screenshot'] = str(context.evidence(context.last_scene, 'error'))
                        result['ocr_text'] = context.last_scene.text
                    except Exception as error:
                        result['evidence_error'] = str(error)
                runner.report.finished_at = timestamp().isoformat()
                result['report'] = runner.report.to_dict()
                self.detach_runner(runner)
                try:
                    write_json(Path(runner.report.run_dir) / 'report.json', result['report'])
                except OSError as error:
                    result['report_write_error'] = str(error)
            write_json(self.directory / 'result.json', result)
            self.completed_requests += 1
            print(json.dumps({key: value for key, value in result.items() if key not in ('tokens', 'ocr_text', 'report')},
                             ensure_ascii=False), flush=True)
        if runner is not None and runner._stop.is_set() and not self.cancelled.is_set():
            self.cancel('runner-stopped')
        return should_quit

    def run(self) -> int:
        mutex = monitor = None
        published_ready = False
        status = 'ended'
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            kernel32.CreateMutexW.argtypes = [C.c_void_p, C.c_int, C.c_wchar_p]
            kernel32.CreateMutexW.restype = C.c_void_p
            C.set_last_error(0)
            mutex = kernel32.CreateMutexW(None, False, 'Local\\AutoZZZ.DailyRunner')
            already_running = C.get_last_error() == 183
            if not mutex or already_running:
                raise AutomationError('另一个 AutoZZZ 任务或校准会话正在运行，请先停止后重试。')
            # Keep the same mutex handle through all idle/action/pause/cleanup.
            monitor = threading.Thread(target=self.watchdog, name='AutoZZZ-session-watchdog', daemon=True)
            monitor.start()
            vision = Vision()
            self.checkpoint()
            write_json(self.directory / 'ready.json', {
                'pid': os.getpid(), 'session_id': self.session_id, 'started_at': self.started.isoformat(),
                'expires_in_seconds': SESSION_SECONDS,
                'expires_at': (self.started + timedelta(seconds=SESSION_SECONDS)).isoformat(),
            })
            published_ready = True
            while not self.cancelled.is_set():
                self.checkpoint()
                request = self.directory / 'request.json'
                try:
                    payload = request.read_text(encoding='utf-8-sig')
                except OSError:
                    self.cancelled.wait(0.25)
                    continue
                if payload == self.last_payload:
                    self.cancelled.wait(0.25)
                    continue
                self.last_payload = payload
                try:
                    data = json.loads(payload)
                except ValueError as error:
                    write_json(self.directory / 'result.json', {'id': '', 'status': 'error', 'error': str(error)})
                    continue
                identifier = data.get('id') if isinstance(data, dict) else None
                if isinstance(identifier, str) and identifier:
                    if identifier in self.seen:
                        continue
                    self.seen.add(identifier)
                if self.handle_request(data, vision):
                    break
            return 0
        except Stopped as error:
            print(str(error), flush=True)
            return 0
        except Exception as error:
            status = 'failed'
            print(f'Session failed: {error}', flush=True)
            if not self.reason:
                self.reason = str(error)
            return 1
        finally:
            self.cancel(self.reason or 'ended')
            if monitor is not None:
                monitor.join(timeout=1)
            try:
                write_json(self.directory / 'ended.json', {
                    'pid': os.getpid(), 'session_id': self.session_id, 'status': status, 'reason': self.reason,
                    'started_at': self.started.isoformat(), 'ended_at': timestamp().isoformat(),
                    'completed_requests': self.completed_requests,
                })
            finally:
                try:
                    if published_ready:
                        ready = self.directory / 'ready.json'
                        try:
                            data = json.loads(ready.read_text(encoding='utf-8'))
                            if isinstance(data, dict) and data.get('session_id') == self.session_id:
                                ready.unlink(missing_ok=True)
                        except (OSError, ValueError):
                            ready.unlink(missing_ok=True)
                finally:
                    if mutex:
                        kernel32.CloseHandle(mutex)


def main() -> int:
    directory = ROOT / 'runs' / 'live-session'
    directory.mkdir(parents=True, exist_ok=True)
    previous_stdout, previous_stderr = sys.stdout, sys.stderr
    with (directory / 'session.log').open('a', encoding='utf-8', buffering=1) as log:
        sys.stdout = sys.stderr = log
        try:
            return LiveSession(directory).run()
        finally:
            sys.stdout, sys.stderr = previous_stdout, previous_stderr


if __name__ == '__main__':
    raise SystemExit(main())
