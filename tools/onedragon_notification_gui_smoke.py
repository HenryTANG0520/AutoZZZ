"""通知表单与 24 渠道在三种 DPI 下的布局、请求与关闭检查；无外部发送。"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import tkinter as tk
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from autozzz import windows
from autozzz.config import Settings
from autozzz.gui import AutoZZZWindow
from autozzz.onedragon_notify_ui import ChannelsWindow, open_channels
from onedragon_configuration_gui_smoke import layout, pump


class Session:
    data = None
    def __init__(self, paths, emit):
        self.emit, self.commands = emit, []
        self.finished = threading.Event()
        self.worker = threading.Thread(target=self.work)
        self.worker.start()
        self.emit(dict(type='connected'))
    def work(self):
        self.finished.wait()
        time.sleep(.15)
        self.emit(dict(type='closed'))
    def send(self, command, **params):
        self.commands.append((command, params))
        assert command == 'rpc'
        method, args = params['method'], params['params']
        channels = self.data['channels']
        if method == 'notification_channels': result = [dict(id=d['id'], name=d['name']) for d in channels]
        elif method == 'notification_test': result = dict(sent=1, failed=0, test_result=dict(success=True, detail=''))
        else:
            result = self.data['settings'] if args['channel_id'] == '_settings' else self.data['webhook'] if args['channel_id'] == 'WEBHOOK' else next(d for d in channels if d['id'] == args['channel_id'])
        self.emit(dict(type='rpc_result', tag=method, result=copy.deepcopy(result)))
    def close(self): self.finished.set()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--descriptions', type=Path, required=True)
    parser.add_argument('--preview', type=Path)
    args = parser.parse_args()
    snapshot = json.loads(args.descriptions.read_text(encoding='utf-8'))
    Session.data = snapshot
    cases, checks = 0, []
    with tempfile.TemporaryDirectory(prefix='autozzz-notification-ui-') as folder:
        config = Path(folder)/'config.json'
        Settings().save(config)
        for scale in (1., 1.5, 2.):
            root = tk.Tk()
            root.tk.call('tk', 'scaling', scale * 96 / 72)
            errors = []
            root.report_callback_exception = lambda kind, value, tb: errors.append(value)
            with patch('autozzz.gui.AutoZZZWindow._discover_report'), patch('autozzz.onedragon_notify_ui.RuntimeSession', Session):
                ui = AutoZZZWindow(root, config)
                ui.overlay.set_enabled(False)
                ui._show_page('applications')
                panel = ui.onedragon_panel
                panel.apps = {'notify': dict(id='notify', name='通知')}
                panel.current_id = 'notify'
                panel.render(snapshot['notify'])
                root.update()
                before = {f['name']: f['value'] for f in snapshot['notify']['fields']}
                assert panel.form.values() == before
                for size in ('default', 'minimum'):
                    if size == 'minimum': root.geometry('%dx%d' % root.minsize())
                    root.update()
                    layout(root)
                    cases += 1
                panel.form.lifecycle.variable.set('仅结束')
                panel.form.detail.variable.set('仅失败')
                panel.form.all()
                assert all(r['lifecycle'] == 'finish_only' and r['detail'] == 'error_only' for r in panel.form.values()['notification_modes'])
                panel.dirty = False
                dialog = ChannelsWindow(ui, 'global')
                ui.notification_window = dialog
                pump(root, lambda: dialog.description is not None and not dialog.busy)
                assert dialog.description['id'] == '_settings'
                for size in ('default', 'minimum'):
                    if size == 'minimum': dialog.window.geometry('%dx%d' % dialog.window.minsize())
                    for description in [snapshot['settings'], *snapshot['channels']]:
                        dialog.current_id = description['id']
                        dialog.render(copy.deepcopy(description))
                        root.update()
                        layout(dialog.window)
                        dialog.values()
                        cases += 1
                dialog.current_id = 'WEBHOOK'
                dialog.render(copy.deepcopy(snapshot['webhook']))
                root.update()
                assert dialog.selector.get() == '通用Webhook'
                assert not dialog.dirty
                dialog.show_inputs.set(True)
                dialog.visibility()
                assert dialog.inputs['URL'][2].cget('show') == '' and not dialog.dirty
                entry = dialog.inputs['URL'][2]
                entry.insert(0, 'https://ui-fixture.invalid/new')
                assert dialog.dirty
                dialog.save_button.invoke()
                assert dialog.busy and entry.instate(['disabled'])
                command = dialog.session.commands[-1][1]
                assert command['method'] == 'notification_channel_save'
                assert command['params']['values']['URL'] == 'https://ui-fixture.invalid/new'
                pump(root, lambda: not dialog.busy)
                dialog.test_button.invoke()
                request = dialog.session.commands[-1][1]
                assert request['method'] == 'notification_test' and set(request['params']) == {'channel_id', 'revision'}
                pump(root, lambda: not dialog.busy)
                assert dialog.status.get() == '测试通知已发送'
                if args.preview is not None and scale == 1.:
                    from PIL import ImageGrab
                    args.preview.parent.mkdir(parents=True, exist_ok=True)
                    root.update()
                    x, y = dialog.window.winfo_rootx(), dialog.window.winfo_rooty()
                    ImageGrab.grab((x, y, x+dialog.window.winfo_width(), y+dialog.window.winfo_height())).save(args.preview)
                dialog.close()
                assert not dialog.closed and dialog.worker.is_alive()
                pump(root, lambda: dialog.closed)
                if scale == 1.:
                    first = open_channels(ui, 'cn')
                    pump(root, lambda: first.description is not None and not first.busy)
                    open_channels(ui, 'global')
                    pump(root, lambda: ui.notification_window is not first and ui.notification_window.description is not None)
                    assert first.closed and not first.worker.is_alive() and ui.notification_window.profile == 'global'
                    ui.notification_window.close()
                    pump(root, lambda: ui.notification_window.closed)
                assert not errors, errors
                checks.append(dict(scale=scale, app_modes=len(panel.form.rows), save_and_test_requests=True, close_waited=True))
                ui._close()
    print(json.dumps(dict(layout_cases=cases, checks=checks, external_messages_sent=False, game_accessed=False), ensure_ascii=False))


if __name__ == '__main__': main()
