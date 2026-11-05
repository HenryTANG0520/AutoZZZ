import queue
import threading
import unittest
from unittest.mock import Mock, call, patch

from autozzz.onedragon_mcp_ui import McpWindow


class Value:
    def __init__(self, value=''):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value

    def set_status(self, status, detail):
        self.value = (status, detail)


class Text:
    def __init__(self):
        self.value = ''
        self.view = (0.0, 1.0)
        self.writes = 0
        self.position = None

    def get(self, *_):
        return self.value

    def yview(self):
        return self.view

    def configure(self, **_):
        pass

    def delete(self, *_):
        self.value = ''

    def insert(self, _, value):
        self.value = value
        self.writes += 1

    def yview_moveto(self, position):
        self.position = position


class McpDiagnosticsUiTests(unittest.TestCase):
    def setUp(self):
        patcher = patch('autozzz.onedragon_mcp_ui.application_name', side_effect=lambda _key, name: name)
        patcher.start()
        self.addCleanup(patcher.stop)

    def view(self, rpc):
        view = McpWindow.__new__(McpWindow)
        view.rpc = rpc
        view.events = queue.Queue()
        view.window = Mock()
        view.window.winfo_viewable.return_value = True
        view.window.after.side_effect = iter(range(1, 100))
        view.state = {}
        view.busy = view.closing = view.closed = view._quiet = False
        view.poll_token = view.status_poll_token = view._deferred = view.action_window = None
        view.archive_map = {}
        view.service_buttons = {key: Mock() for key in ('启动', '停止', '重启', '刷新')}
        view.address_button = Mock()
        view.token_button = Mock()
        view.archive_combo = Mock()
        view.details = Mock()
        view.resource_tab = object()
        view.log = Text()
        view.resource_list = Text()
        view.port = Value('23001')
        view.accepted = Value(False)
        for key in ('url', 'token', 'message', 'run_message', 'edit_message', 'prepared', 'archive'):
            setattr(view, key, Value())
        return view

    def snapshot(self, *, running=True, token=None, job='one', resource='review'):
        status = dict(running=running, url='http://127.0.0.1:23001/mcp' if running else '',
                      execution=dict(job_id=job, prepared_app='email', resources=dict(
                          confirmation_required=True, confirmation_id=job, actions=[dict(text=resource)])),
                      run_status=dict(state='focus_wait', source='mcp', app='email', step='fixture step', duration_seconds=12.5),
                      logs=[dict(time=10., message='fixture service event')])
        if token is not None:
            status['bearer_token'] = token
        return status

    @staticmethod
    def deliver(view):
        event = view.events.get(timeout=3)
        view.events.put(event)
        view.poll()

    def test_snapshot_separates_logs_resources_and_clipboard_address(self):
        view = self.view(Mock())
        value = self.snapshot(token='fixture-credential')
        view._apply_status(value)
        self.assertNotIn('bearer_token', view.state)
        self.assertIn('] fixture service event', view.log.value)
        self.assertNotIn('review', view.log.value)
        self.assertNotIn('fixture-credential', view.log.value)
        self.assertEqual(view.resource_list.value, 'review')
        self.assertEqual(view.run_message.get(), ('等待游戏恢复焦点', 'MCP · email · fixture step · 12.5s'))
        view.copy_address()
        view.window.clipboard_append.assert_called_once_with('http://127.0.0.1:23001/mcp')
        view.log.view = (0.25, 0.75)
        view._apply_status(value)
        self.assertEqual(view.log.writes, 1)
        value['logs'].append(dict(time=11., message='new event'))
        view._apply_status(value)
        self.assertEqual(view.log.position, 0.25)

    def test_restart_serializes_stop_and_start_and_replaces_token(self):
        for restart_fails in (False, True):
            with self.subTest(restart_fails=restart_fails):
                started = self.snapshot(token='new-token')
                next_result = RuntimeError('fixture port refused old-token') if restart_fails else started
                rpc = Mock(side_effect=[self.snapshot(running=False), next_result])
                view = self.view(rpc)
                view._apply_status(self.snapshot(token='old-token'))
                old_action = Mock()
                view.action_window = old_action
                view.start(restart=True)
                self.deliver(view)
                self.assertEqual(rpc.call_args_list, [call('mcp_stop', {}), call('mcp_start', {'port': 23001})])
                self.assertEqual(view.state['running'], not restart_fails)
                self.assertEqual(view.token.get(), '' if restart_fails else 'new-token')
                self.assertNotIn('old-token', view.message.get())
                old_action.destroy.assert_called_once()
                self.assertIsNone(view.action_window)

    def test_visible_refresh_coalesces_and_close_is_not_lost_behind_it(self):
        entered, release = threading.Event(), threading.Event()

        def handle(method, _):
            if method == 'mcp_status':
                entered.set()
                if not release.wait(3):
                    raise RuntimeError('fixture refresh timed out')
                return self.snapshot()
            return self.snapshot(running=False)

        rpc = Mock(side_effect=handle)
        view = self.view(rpc)
        view.window.winfo_viewable.return_value = False
        view.refresh_status()
        rpc.assert_not_called()
        view.window.winfo_viewable.return_value = True
        view.refresh_status()
        self.assertTrue(entered.wait(2))
        try:
            view.refresh_status()
            view.call('mcp_status')
            view.call('mcp_edit_disable')
            view.close()
            rpc.assert_called_once_with('mcp_status', {})
        finally:
            release.set()
        self.deliver(view)
        self.deliver(view)
        self.assertEqual(rpc.call_args_list, [call('mcp_status', {}), call('mcp_stop', {})])
        self.assertTrue(view.closed)
        self.assertIsNone(view.status_poll_token)
        view.window.destroy.assert_called_once()

    def test_changed_resources_clear_consent_and_drop_grant_waiting_on_refresh(self):
        release = threading.Event()

        def handle(*_):
            if not release.wait(3):
                raise RuntimeError('fixture refresh timed out')
            return self.snapshot(job='two', resource='review changed')

        rpc = Mock(side_effect=handle)
        view = self.view(rpc)
        view._apply_status(self.snapshot())
        view.accepted.set(True)
        view.refresh_status()
        try:
            view.grant()
        finally:
            release.set()
        self.deliver(view)
        rpc.assert_called_once_with('mcp_status', {})
        self.assertFalse(view.accepted.get())
        self.assertEqual(view.resource_list.value, 'review changed')
        self.assertIn('重新核对', view.message.get())


if __name__ == '__main__':
    unittest.main()
