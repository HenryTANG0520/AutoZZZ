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
        self.binding, self.finish_binding, self.status_error = None, False, False
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
        elif method == 'qq_bind_start':
            self.binding = dict(job_id='original-ui-binding', state='waiting', code='2468', target='group' if args['is_group'] else 'user',
                                busy=True, saved=False, remaining_seconds=60, detail='请在群里 @机器人 发送匹配码。' if args['is_group'] else '请私聊机器人发送匹配码。')
            result = self.binding
        elif method in ('qq_bind_status', 'qq_bind_cancel'):
            assert args == dict(job_id='original-ui-binding')
            if self.status_error:
                self.status_error = False
                self.emit(dict(type='error', message='模拟状态查询失败', busy=False))
                return
            if method == 'qq_bind_cancel': self.binding.update(state='cancelling', code='', detail='正在取消，等待原连接退出…')
            if self.finish_binding:
                cancelled = self.binding['state'] == 'cancelling'
                self.binding.update(state='cancelled' if cancelled else 'success', code='', busy=False, saved=not cancelled,
                                    detail='已取消绑定。' if cancelled else '群聊收件方已保存。')
            result = self.binding
        else:
            result = self.data['settings'] if args['channel_id'] == '_settings' else self.data['webhook'] if args['channel_id'] == 'WEBHOOK' else next(d for d in channels if d['id'] == args['channel_id'])
        self.emit(dict(type='rpc_result', tag=method, result=copy.deepcopy(result)))
    def close(self): self.finished.set()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--descriptions', type=Path, required=True)
    parser.add_argument('--preview', type=Path)
    parser.add_argument('--binding-descriptions', type=Path)
    args = parser.parse_args()
    snapshot = json.loads(args.descriptions.read_text(encoding='utf-8'))
    if args.binding_descriptions:
        extra = json.loads(args.binding_descriptions.read_text(encoding='utf-8'))
        snapshot['channels'] = [extra['qq'] if c['id'] == 'QQ_BOT' else c for c in snapshot['channels']]
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
                if args.binding_descriptions:
                    qq = next(c for c in snapshot['channels'] if c['id'] == 'QQ_BOT')
                    dialog.render(copy.deepcopy(qq))
                    dialog.binding_buttons[0].invoke()
                    pump(root, lambda: dialog.binding is not None and not dialog.busy)
                    assert '2468' in dialog.status.get() and dialog.cancel_binding_button.winfo_ismapped()
                    assert all(entry[2].instate(['disabled']) for entry in dialog.inputs.values() if len(entry) == 3)
                    assert dialog.save_button.instate(['disabled']) and dialog.test_button.instate(['disabled'])
                    layout(dialog.window); cases += 1
                    dialog.session.status_error = True
                    pump(root, lambda: '模拟状态查询失败' in dialog.status.get())
                    assert dialog.binding['job_id'] == 'original-ui-binding' and dialog.inputs['APP_ID'][2].instate(['disabled'])
                    assert len([c for c in dialog.session.commands if c[1]['method'] == 'qq_bind_start']) == 1
                    dialog.cancel_binding_button.invoke()
                    pump(root, lambda: dialog.binding is not None and dialog.binding['state'] == 'cancelling')
                    assert dialog.cancel_binding_button.instate(['disabled']) and not dialog.closed
                    layout(dialog.window); cases += 1
                    dialog.session.finish_binding = True
                    pump(root, lambda: dialog.binding is None and not dialog.busy)
                    assert dialog.status.get() == '已取消绑定。' and not dialog.cancel_binding_button.winfo_ismapped()
                    assert not dialog.inputs['APP_ID'][2].instate(['disabled'])
                    dialog.session.finish_binding = False
                    dialog.binding_buttons[1].invoke()
                    pump(root, lambda: dialog.binding is not None and not dialog.busy)
                    assert dialog.binding['target'] == 'group'
                    dialog.session.finish_binding = True
                    pump(root, lambda: dialog.binding is None and not dialog.busy)
                    assert dialog.status.get() == '群聊收件方已保存。'
                    assert len([c for c in dialog.session.commands if c[1]['method'] == 'qq_bind_start']) == 2
                    checks.append(dict(scale=scale, binding_cancel=True, observation_failure_keeps_job=True, binding_saved_refresh=True))
                if args.preview is not None and scale == 1.:
                    from PIL import ImageGrab
                    args.preview.parent.mkdir(parents=True, exist_ok=True)
                    root.update()
                    ImageGrab.grab(window=dialog.window.winfo_id()).save(args.preview)
                if args.binding_descriptions:
                    dialog.session.finish_binding = False
                    dialog.binding_buttons[0].invoke()
                    pump(root, lambda: dialog.binding is not None and not dialog.busy)
                dialog.close()
                if args.binding_descriptions: assert '取消绑定' in dialog.status.get()
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
