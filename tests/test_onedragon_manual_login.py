"""Replay the legacy entry and SDK handoff with simulated OCR, time and window I/O."""
import contextlib
import io
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from autozzz.app import Context, Runner
from autozzz.config import Settings
from autozzz.manual_login import ManualLoginWait, is_global_login_form
from autozzz.models import AutomationError, Scene
from autozzz.onedragon_cli import run_application
from autozzz.onedragon_execution import OneDragonExecution
from autozzz.onedragon_resources import ResourceConfirmation
from autozzz.onedragon_session import RuntimeSession
from autozzz.workflows import DailyWorkflow
from tests.test_core import game_start_page, scene, token, world_with_npc
from tests.test_focus_resume import GEOMETRY, VisibleWindow
from tests.test_onedragon_session import Client


def login_form(filled=False):
    fields = [] if filled else [token('输入邮箱/用户名', .38, .366), token('输入密码', .37, .46)]
    return Scene([token('HOYOVERSE', .5, .25), *fields, token('立即注册', .347, .542),
                  token('忘记密码', .657, .542), token('进入游戏', .50, .623)], geometry=GEOMETRY)


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, interval):
        self.now += max(interval, 0.00001)


class EntryClient(Client):
    manual_login_handoff_supported = True

    def submit(self, method, **params):
        if method == 'execution_status' and self.state == 'running':
            self.state = 'success'
        request = super().submit(method, **params)
        if method == 'execution_status' and self.state == 'prepared':
            self.requests[request][1].update(
                resources=dict(confirmation_required=True, confirmation_id='prepared-token'),
                manual_login_handoff_supported=self.manual_login_handoff_supported,
            )
        elif method == 'game_launch_options':
            self.requests[request] = (method, {})
        return request


class ManualLoginTests(unittest.TestCase):
    def setUp(self):
        stack = self.enterContext(contextlib.ExitStack())
        self.folder = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        self.game = str(self.folder / 'ZenlessZoneZero.exe')
        self.window = VisibleWindow()
        self.clock = Clock()
        for module in ('app', 'manual_login'):
            stack.enter_context(patch(f'autozzz.{module}.time', self.clock))
        self.process = dict(pid=57, created=123456, path=self.game)
        self.observe_owner = stack.enter_context(patch('autozzz.process_inventory.window_process_identity',
                                                       return_value=self.process))
        self.vision = Mock()

    def workflow(self, frames, seconds=60):
        wait = None if seconds is None else ManualLoginWait(seconds)
        runner = Runner(Settings(server='global', game_path=self.game, transition_timeout=10, auto_launch=True),
                        manual_login=wait)
        if wait is not None:
            wait.bind(self.window, self.game)
        runner._window = self.window
        self.vision.read.side_effect = frames
        context = Context(runner, self.window, self.vision)
        context.template = Mock(return_value=None)
        context.evidence = Mock()
        return DailyWorkflow(context)

    def test_form_is_read_only_and_does_not_consume_normal_transition_budget(self):
        self.assertFalse(is_global_login_form(login_form(filled=True)))
        self.assertTrue(is_global_login_form(login_form(filled=True), already_waiting=True))
        title, world = game_start_page(), world_with_npc()
        workflow = self.workflow([scene(), login_form(), *[login_form(filled=True)] * 18,
                                  scene(), title, world, world])
        self.assertEqual(workflow.ensure_main().status, 'ok')
        self.assertGreater(self.clock.now, 10)
        self.window.click.assert_called_once_with(.5, .82, title.geometry)
        self.window.key.assert_not_called()
        self.assertFalse(workflow.c.evidence.called)

    def test_manual_login_keeps_credential_frames_out_of_evidence(self):
        workflow = self.workflow([login_form()])
        with patch('autozzz.app.save_scene') as save:
            self.assertIsNone(Context.evidence(workflow.c, login_form(filled=True), 'unknown_page'))
            save.assert_not_called()

    def test_ordinary_entry_and_an_unrelated_password_page_keep_legacy_failure(self):
        self.assertFalse(is_global_login_form(scene('HOYOVERSE', '输入密码', '点击登录')))
        for form, seconds in ((login_form(), None), (scene('输入密码'), 60)):
            with self.subTest(seconds=seconds), self.assertRaisesRegex(AutomationError, '需要手动完成登录验证'):
                self.workflow([form], seconds).ensure_main()
        self.window.click.assert_not_called()
        self.window.key.assert_not_called()

    def test_partial_form_does_not_become_a_cached_account_click(self):
        partial = Scene([token('HOYOVERSE', .5, .25), token('进入游戏', .5, .623),
                         token('fixture****@example.test', .40, .43)], geometry=GEOMETRY)
        workflow = self.workflow([partial])
        self.assertIsNotNone(workflow._cached_login(partial))
        with self.assertRaisesRegex(AutomationError, '疑似 HOYOVERSE 人工登录表单'):
            workflow.ensure_main()
        self.window.click.assert_not_called()

    def test_protocol_or_update_controls_exit_manual_wait_into_old_failure(self):
        for blocker in ('请同意用户协议', '确认更新', '输入验证码'):
            blocked = login_form()
            blocked.tokens.append(token(blocker))
            with self.subTest(blocker=blocker), self.assertRaisesRegex(AutomationError, '需要手动完成登录验证'):
                self.workflow([login_form(), blocked]).ensure_main()
        self.window.click.assert_not_called()
        self.window.key.assert_not_called()

    def test_network_retry_still_uses_only_the_old_verified_button(self):
        network = Scene([token('网络请求错误，请稍后尝试', .5, .45), token('重试', .5, .61)], geometry=GEOMETRY)
        world = world_with_npc()
        workflow = self.workflow([login_form(), network, world, world])
        self.assertEqual(workflow.ensure_main().status, 'ok')
        self.window.click.assert_called_once_with(.5, .61, network.geometry)
        self.window.key.assert_not_called()

    def test_timeout_keeps_running_when_paused_or_out_of_focus(self):
        for paused in (False, True):
            with self.subTest(paused=paused):
                self.window.ready.set()
                workflow = self.workflow([login_form()], seconds=1)
                runner = workflow.c.runner
                runner.log = Mock(side_effect=lambda *a: runner._pause.set() if paused else self.window.ready.clear())
                runner._stop.wait = self.clock.sleep
                started = self.clock.now
                with self.assertRaisesRegex(AutomationError, '手动登录国际服超时'):
                    workflow.ensure_main()
                self.assertLess(self.clock.now - started, 1.11)
                self.assertIsNone(workflow.c.last_scene)
                self.window.click.assert_not_called()
                self.window.key.assert_not_called()

    def test_returning_to_form_after_unknown_frame_does_not_extend_deadline(self):
        workflow = self.workflow([login_form(), scene(), login_form()], seconds=1)
        with self.assertRaisesRegex(AutomationError, '手动登录国际服超时'):
            workflow.ensure_main()
        self.assertLess(self.clock.now, 1.11)
        self.window.click.assert_not_called()

    def test_focus_change_during_ocr_discards_the_old_exit_frame(self):
        stale = game_start_page()
        current = Scene([token('点击进入游戏', .55, .82)], geometry=GEOMETRY)
        world = world_with_npc()
        frames = iter([login_form(), stale, current, world, world])
        workflow = self.workflow([])

        def read(*_):
            frame = next(frames)
            if frame is stale:
                self.window.ready.clear()
            return frame

        self.vision.read.side_effect = read
        workflow.c.runner._stop.wait = lambda _: self.window.ready.set()
        self.assertEqual(workflow.ensure_main().status, 'ok')
        self.window.click.assert_called_once_with(.55, .82, current.geometry)
        self.window.focus.assert_not_called()

    def test_same_hwnd_pid_with_replaced_creation_time_cannot_resume(self):
        workflow = self.workflow([login_form(), game_start_page()])
        workflow.c.runner.log = Mock(side_effect=lambda *_: setattr(
            self.observe_owner, 'return_value', dict(self.process, created=123457)))
        with self.assertRaisesRegex(AutomationError, '进程身份已变化'):
            workflow.ensure_main()
        self.window.click.assert_not_called()
        self.window.key.assert_not_called()

    def run_session(self, *, stop=False, confirm=True, supported=True):
        Client.instances = []
        world = world_with_npc()
        self.vision.read.side_effect = [login_form(), game_start_page(), game_start_page(), world, world]

        def new_runner(settings, emit, **kwargs):
            def event(event):
                if stop and event.get('type') == 'log' and 'HOYOVERSE 登录表单' in event.get('message', ''):
                    runner.stop()  # Same runner stop signal used by the F10 monitor.
                emit(event)

            runner = Runner(settings, event, **kwargs)
            runner._hotkeys = Mock()
            return runner

        paths = SimpleNamespace(data=self.folder, stable_data=None, generation='-', management_only=False)
        with (patch('autozzz.onedragon_session.OneDragonClient', EntryClient),
              patch.object(EntryClient, 'manual_login_handoff_supported', supported),
              patch('autozzz.onedragon_cli.RuntimePaths.discover', return_value=paths),
              patch('autozzz.app.Runner', side_effect=new_runner),
              patch('autozzz.app.ROOT', self.folder),
              patch('autozzz.app.launch_game', return_value='offline fixture') as launch,
              patch('autozzz.app.find_game', return_value=self.window),
              patch('autozzz.app.is_elevated', return_value=False),
              patch('autozzz.app.Vision', return_value=self.vision),
              patch('autozzz.app.Context.template', return_value=None),
              patch('autozzz.app.Context.evidence'),
              patch('autozzz.app.C.get_last_error', return_value=0),
              patch('autozzz.windows.kernel32')):
            if not confirm:
                with self.assertRaisesRegex(ValueError, '核对并确认'):
                    self.start_session(confirm)
                launch.assert_not_called()
                return None
            if not supported:
                with self.assertRaisesRegex(RuntimeError, '桥接不支持人工登录等待'):
                    self.start_session(confirm)
                launch.assert_not_called()
                return None
            result = self.start_session(confirm)
            launch.assert_called_once()
            return result

    def start_session(self, confirm):
        return run_application('one_dragon', server='global', game_path=self.game, region='eu', timeout=3600,
                               manual_login_timeout_seconds=180, confirm_resources=confirm,
                               session_factory=lambda paths, sink: RuntimeSession(paths, sink, self.folder / 'sdk'))

    def test_cli_session_retains_prepared_job_region_and_resource_confirmation(self):
        self.assertEqual(self.run_session()['state']['state'], 'success')
        client, = Client.instances
        self.assertEqual([params for name, params in client.calls if name == 'initialize_execution'],
                         [dict(server='global', game_path=self.game, region='eu', instance_idx=None)])
        self.assertEqual([params for name, params in client.calls if name == 'start_application'], [
            dict(app_id='one_dragon', budget=None, timeout_seconds=3600, resource_confirmation='prepared-token',
                 entry_handoff=dict(prepared_job_id='prep', window_handle=1, process_id=57, process_created=123456))])
        self.window.click.assert_called_once_with(.5, .82, GEOMETRY)

    def test_stopping_entry_never_starts_sdk_application(self):
        self.assertEqual(self.run_session(stop=True)['state']['state'], 'stopped')
        self.assertNotIn('start_application', [name for name, _ in Client.instances[0].calls])
        self.window.click.assert_not_called()

    def test_unconfirmed_resources_do_not_even_launch_entry(self):
        self.run_session(confirm=False)
        self.assertNotIn('start_application', [name for name, _ in Client.instances[0].calls])

    def test_older_bridge_rejects_opt_in_before_game_launch(self):
        self.run_session(supported=False)
        self.assertNotIn('start_application', [name for name, _ in Client.instances[0].calls])

    def test_command_line_accepts_only_the_explicit_bounded_opt_in(self):
        from autozzz.__main__ import main

        for flags in (['--server', 'cn'], ['--server', 'global', '--no-auto-launch'],
                      ['--server', 'global', '--manual-login-timeout', '0'],
                      ['--server', 'global', '--manual-login-timeout', '3601']):
            with self.subTest(flags=flags), contextlib.redirect_stderr(io.StringIO()), \
                    patch('autozzz.onedragon_cli.RuntimePaths.discover') as discover:
                self.assertEqual(main(['--onedragon-run', 'one_dragon', '--manual-login-timeout', '180', *flags]), 2)
                discover.assert_not_called()
        with patch('autozzz.onedragon_cli.run_application', return_value=dict(state=dict(state='success'))) as run, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['--onedragon-run', 'one_dragon', '--server', 'global',
                                   '--manual-login-timeout', '180', '--run-timeout', '3600']), 0)
            self.assertEqual(run.call_args.kwargs['manual_login_timeout_seconds'], 180)
            self.assertEqual(run.call_args.kwargs['timeout'], 3600)


class SdkHandoffTests(unittest.TestCase):
    def test_same_prepared_job_connection_and_resource_token_are_required(self):
        execution = OneDragonExecution.__new__(OneDragonExecution)
        account = SimpleNamespace(game_path='fixture.exe', game_region='asia', game_language='cn')
        execution.ctx = SimpleNamespace(ready_for_application=True, current_instance_idx=2, game_account_config=account,
                                        run_context=SimpleNamespace(is_app_registered=lambda _: True,
                                                                    is_app_internal=lambda _: False))
        execution.connection = (2, 'fixture.exe', 'asia', 'cn')
        execution.rotation_guard, execution.uses_charge = None, False
        execution.prepared_app, execution.prepared_hash = 'one_dragon', 'prepared-config'
        execution.state, execution.job_id = 'prepared', 'original-job'
        execution.lock, execution.closed, execution.worker = threading.RLock(), False, None
        confirmation = execution.resource_confirmation = ResourceConfirmation()
        confirmation.prepare([dict(id='fixture:consume', requires_confirmation=True)], 'prepared-config')
        identity = dict(prepared_job_id='other-job', window_handle=1, process_id=57, process_created=123456)
        with self.assertRaisesRegex(ValueError, '旧确认不能复用'):
            execution.start_application('one_dragon', entry_handoff=identity, resource_confirmation='old-token')
        with self.assertRaisesRegex(RuntimeError, '原准备任务已变化'):
            execution.start_application('one_dragon', entry_handoff=identity, resource_confirmation=confirmation.token)
        account.game_region = 'eu'
        with self.assertRaisesRegex(ValueError, '区服配置已变化'):
            execution.start_application('one_dragon', entry_handoff=identity, resource_confirmation=confirmation.token)
        self.assertIsNone(execution.worker)
        self.assertEqual(confirmation.accepted, [])

    def test_sdk_rebind_rejects_recycled_game_process_before_starting_operation(self):
        execution = OneDragonExecution.__new__(OneDragonExecution)
        window = SimpleNamespace(bound=SimpleNamespace(hwnd=1), process_id=57, identity_valid=lambda: True,
                                 clear_binding=Mock())
        controller = SimpleNamespace(bind=Mock(), game_win=window)
        execution.ctx = SimpleNamespace(controller=controller, game_account_config=SimpleNamespace(game_path='fixture.exe'),
                                        push_service=SimpleNamespace(reset_results=Mock()))
        execution.backend = Mock()
        with patch('autozzz.onedragon_startup.game_processes', return_value=[dict(pid=57, created=123457)]):
            with self.assertRaisesRegex(RuntimeError, '游戏进程身份已变化'):
                execution._run_bound_app('one_dragon', expected_window=dict(window_handle=1, process_id=57,
                                                                           process_created=123456))
        controller.bind.assert_called_once_with('fixture.exe')
        window.clear_binding.assert_called_once()
        execution.backend._start_app.assert_not_called()
