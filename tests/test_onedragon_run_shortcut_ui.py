from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from autozzz.onedragon_run_shortcut_ui import PreparedRunShortcut
from autozzz.run_hotkeys import F9, F10, RunKeyEdge


class PreparedRunShortcutTests(unittest.TestCase):
    def setUp(self):
        self.now = 1.0
        self.button_enabled = True
        self.own_window = 41
        self.game_window = 42
        self.game = str(Path(__file__).parent / 'fixture_game' / 'ZenlessZoneZero.exe')
        self.native = SimpleNamespace(GetForegroundWindow=Mock(return_value=self.own_window),
                                      GetAncestor=Mock(return_value=self.own_window))
        self.process_path = Mock(return_value=self.game)
        for target, replacement in (('user32', self.native), ('process_path', self.process_path)):
            override = patch(f'autozzz.windows.{target}', replacement)
            override.start()
            self.addCleanup(override.stop)

        self.button = SimpleNamespace(instate=lambda _: self.button_enabled, invoke=Mock())
        self.window = SimpleNamespace(winfo_id=lambda: 40, winfo_viewable=lambda: True,
                                      grab_current=Mock(return_value=None), focus_get=Mock(return_value=None))
        self.view = SimpleNamespace(prepared=True, busy=False, closing=False, ui=SimpleNamespace(closing=False),
                                    window=self.window, start_button=self.button, last_state={'state': 'prepared'},
                                    game_path=SimpleNamespace(get=lambda: self.game), app_id='one_dragon',
                                    finish_display=None, cancel_finish=Mock())
        self.source = Mock()
        self.shortcut = PreparedRunShortcut(self.view, source_factory=lambda _: self.source, clock=lambda: self.now)
        self.addCleanup(self.shortcut.close)
        self.shortcut.configure({'start': 'P', 'stop': 'F8'})
        self.poll()

    def edge(self, down, *, action='start', code=ord('P'), press_id=1, foreground=None, created=None,
             source='hook', injected=False):
        return RunKeyEdge(action, down, self.now + .01 if created is None else created,
                          self.native.GetForegroundWindow.return_value if foreground is None else foreground,
                          code, 1, press_id, source, injected, False, 0)

    def poll(self, *edges):
        self.now += .1
        self.source.drain.return_value = list(edges)
        self.shortcut.poll()

    def tap(self, **kwargs):
        self.poll(self.edge(True, **kwargs), self.edge(False, **kwargs))

    def test_fresh_press_requires_matching_release_and_only_starts_once(self):
        self.poll(self.edge(True))
        self.poll(self.edge(False, press_id=2))
        self.button.invoke.assert_not_called()
        self.poll(self.edge(False), self.edge(False))
        self.button.invoke.assert_called_once_with()

    def test_new_preparation_rejects_a_press_held_from_the_previous_one(self):
        self.poll(self.edge(True))
        self.view.last_state = {'state': 'prepared'}
        self.poll()
        self.poll(self.edge(False))
        self.button.invoke.assert_not_called()
        self.tap(press_id=2)
        self.button.invoke.assert_called_once_with()

    def test_rejects_a_press_from_before_arming_and_an_expired_pair(self):
        self.poll(self.edge(True, created=self.now - .05), self.edge(False))
        self.now += .6
        self.tap(press_id=2, created=self.now - .6)
        self.button.invoke.assert_not_called()

    def test_obeys_the_button_resource_gate_busy_state_and_modal_dialog(self):
        self.button_enabled = False
        self.tap()
        self.button_enabled = True
        self.view.busy = True
        self.tap(press_id=2)
        self.view.busy = False
        self.window.grab_current.return_value = object()
        self.tap(press_id=3)
        self.button.invoke.assert_not_called()
        self.window.grab_current.return_value = None
        self.poll()
        self.tap(press_id=4)
        self.button.invoke.assert_called_once_with()

    def test_focus_change_between_press_and_release_prevents_start(self):
        self.poll(self.edge(True))
        self.native.GetForegroundWindow.return_value = self.game_window
        self.poll(self.edge(False, foreground=self.own_window))
        self.native.GetForegroundWindow.return_value = self.own_window
        self.poll(self.edge(True, press_id=2))
        self.native.GetForegroundWindow.return_value = self.game_window
        self.poll(self.edge(False, press_id=2))
        self.button.invoke.assert_not_called()

    def test_background_requires_the_configured_game_and_a_hook_edge(self):
        self.native.GetForegroundWindow.return_value = self.game_window
        self.process_path.return_value = str(Path(self.game).parent / 'other' / 'ZenlessZoneZero.exe')
        self.tap()
        self.process_path.return_value = self.game
        self.tap(press_id=2, source='polling')
        self.tap(press_id=3, injected=True)
        self.button.invoke.assert_not_called()
        self.tap(press_id=4)
        self.button.invoke.assert_called_once_with()

    def test_editable_field_blocks_custom_aliases_and_keeps_fixed_f9(self):
        class Editable:
            pass

        with patch('autozzz.onedragon_run_shortcut_ui.ttk.Entry', Editable):
            self.window.focus_get.return_value = Editable()
            for alias, code in (('P', ord('P')), ('Ctrl+V', ord('V')), ('F6', 0x75)):
                with self.subTest(alias=alias):
                    self.shortcut.configure({'start': alias, 'stop': 'F8'})
                    self.poll()
                    self.tap(code=code)
                    self.button.invoke.assert_not_called()
            self.tap(action='f9', code=F9, press_id=2)
        self.button.invoke.assert_called_once_with()

    def test_mouse_binding_starts_in_game_but_an_ordinary_form_click_does_not(self):
        self.shortcut.configure({'start': 'mouse_left', 'stop': 'F8'})
        self.poll()
        self.tap(code=1)
        self.button.invoke.assert_not_called()
        self.native.GetForegroundWindow.return_value = self.game_window
        self.tap(code=1, press_id=2)
        self.button.invoke.assert_called_once_with()

    def test_stop_takes_priority_over_start_and_cancels_a_pending_finish(self):
        self.poll(self.edge(True), self.edge(False), self.edge(True, action='stop', code=0x77, press_id=2))
        self.button.invoke.assert_not_called()
        self.button_enabled = False
        self.view.finish_display = SimpleNamespace(state='pending')
        self.poll(self.edge(True, action='f10', code=F10, press_id=3))
        self.view.cancel_finish.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
