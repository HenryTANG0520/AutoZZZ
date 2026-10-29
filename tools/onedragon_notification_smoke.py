"""实际通知配置、生命周期钩子、节点与传输接口回放；禁止外部发送。"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import copy
import io
import json
import logging
from pathlib import Path
import shutil
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import PropertyMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def wait(predicate, timeout=30):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline: raise AssertionError("通知回放超时")
        time.sleep(.01)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', type=Path, required=True)
    args = parser.parse_args()
    marker = args.data / '.autozzz-notification-test'
    if args.data.exists() and any(args.data.iterdir()) and not marker.is_file():
        raise SystemExit('请使用空目录或本工具已有的测试目录。')
    args.data.mkdir(parents=True, exist_ok=True)
    marker.write_text('isolated-notification-test', encoding='utf-8')
    root = Path(__file__).resolve().parents[1]
    from autozzz.config import GAME_PATHS
    from autozzz.onedragon_runtime import RuntimePaths
    defaults = RuntimePaths.discover(root)
    RuntimePaths(defaults.source, defaults.python, args.data.resolve()).prepare()
    cache = root / 'runs/onedragon-20261029/hunt-preparation/assets/models'
    if cache.is_dir(): shutil.copytree(cache, args.data / 'assets/models', dirs_exist_ok=True)
    sys.path.insert(0, str(defaults.source / 'src'))
    import numpy as np
    import requests
    from autozzz.onedragon_service import OneDragonService
    from autozzz.onedragon_notify import NotificationStore
    from autozzz.onedragon_control import WindowState
    from one_dragon.base.operation.operation_notify import send_application_notify, send_node_notify, node_notify, NotifyTiming
    from one_dragon.base.operation.operation_node import OperationNode
    from one_dragon.base.operation.operation_round_result import OperationRoundResult, OperationRoundResultEnum
    from zzz_od.application.notify.notify_app import NotifyApp
    from one_dragon.utils.log_utils import log

    service = OneDragonService(defaults.source, args.data.resolve())
    ctx = service.ctx
    store = NotificationStore(service)
    push = ctx.push_service
    checks, transmissions = [], []
    capture = io.StringIO()
    handler = logging.StreamHandler(capture)
    log.addHandler(handler)
    try:
        ctx.notify_config.enable_notify = False
        push.push_config.send_image = False
        channels = store.channels()
        assert len(channels) == 24
        snapshots = []
        for channel in channels:
            description = store.describe_channel(channel['id'])
            values = dict(enabled=False)
            unique = 'fixture-' + channel['id']
            for field in description['fields']:
                if field['kind'] == 'enum': values[field['name']] = field['value'] or field['options'][0]['value']
                elif field['name'] == 'HEADERS': values[field['name']] = 'Authorization: ' + unique
                elif field['name'] == 'BODY': values[field['name']] = '{"text":"$content"}'
                else: values[field['name']] = unique
            saved = store.save_channel(channel['id'], values, description['revision'])
            assert unique not in json.dumps(saved)
            snapshots.append(saved)
        checks.append('all_24_actual_channel_schemas_roundtrip_without_credential_readback')
        description = store.describe()
        raw = Path(ctx.notify_config.file_path).read_bytes()
        try:
            store.save(dict(enable_notify=True), description['revision'])
            raise AssertionError('无可用渠道仍启用自动通知')
        except ValueError: pass
        assert Path(ctx.notify_config.file_path).read_bytes() == raw
        checks.append('disabled_master_and_screenshots_no_implicit_channel_activation')

        webhook = store.describe_channel('WEBHOOK')
        saved = store.save_channel('WEBHOOK', dict(enabled=True, URL='https://autozzz-notification.invalid/fixture-token',
            METHOD='POST', CONTENT_TYPE='application/json', HEADERS='Authorization: Bearer fixture-secret', BODY='{"text":"$content","image":"$image"}'), webhook['revision'])
        secret_before = push.get_channel_config('WEBHOOK')
        saved = store.save_channel('WEBHOOK', dict(URL=''), saved['revision'])
        assert push.get_channel_config('WEBHOOK') == secret_before
        raw = Path(push.push_config.file_path).read_bytes()
        for values, revision in ((dict(clear_URL=True, URL='new'), saved['revision']),
                                 (dict(unknown='not allowed'), saved['revision']),
                                 (dict(enabled=False), webhook['revision'])):
            try:
                store.save_channel('WEBHOOK', values, revision)
                raise AssertionError('无效或旧版本修改未被拒绝')
            except ValueError: pass
            assert Path(push.push_config.file_path).read_bytes() == raw
        checks.append('blank_preserves_saved_secrets_and_shared_file_revision_prevents_overwrite')

        cfg = store.describe()
        modes = copy.deepcopy(cfg['fields'][-1]['value'])
        for row in modes: row.update(lifecycle='finish_only', detail='off')
        store.save(dict(enable_notify=True, notification_modes=modes), cfg['revision'])
        assert ctx.notify_config.get_app_notify_modes('email') == ('finish_only', 'off')
        checks.append('actual_notify_config_saves_every_application_mode')

        def request(**kwargs):
            assert kwargs['url'].startswith('https://autozzz-notification.invalid/')
            assert not any(kwargs['proxies'].values()), kwargs['proxies']
            transmissions.append(copy.deepcopy(kwargs))
            return SimpleNamespace(status_code=204, raise_for_status=lambda: None)

        with ExitStack() as stack:
            stack.enter_context(patch('zzz_od.operation.enter_game.open_game.OpenGame.execute', side_effect=AssertionError('禁止启动游戏')))
            stack.enter_context(patch('zzz_od.operation.enter_game.open_game.subprocess.Popen', side_effect=AssertionError('禁止启动外部进程')))
            stack.enter_context(patch('winreg.SetValueEx', side_effect=AssertionError('禁止修改注册表')))
            stack.enter_context(patch('winreg.DeleteValue', side_effect=AssertionError('禁止修改注册表')))
            stack.enter_context(patch('requests.sessions.Session.request', side_effect=AssertionError('禁止真实网络请求')))
            stack.enter_context(patch('requests.request', side_effect=request))
            stack.enter_context(patch('smtplib.SMTP', side_effect=AssertionError('禁止真实 SMTP')))
            stack.enter_context(patch('smtplib.SMTP_SSL', side_effect=AssertionError('禁止真实 SMTP')))
            stack.enter_context(patch('websocket.create_connection', side_effect=AssertionError('禁止网关连接')))
            stack.enter_context(patch('socket.socket.connect', side_effect=AssertionError('禁止真实网络连接')))
            stack.enter_context(patch('socket.socket.connect_ex', side_effect=AssertionError('禁止真实网络连接')))
            stack.enter_context(patch('socket.getaddrinfo', side_effect=AssertionError('禁止真实 DNS 查询')))
            image_conversion = stack.enter_context(patch.object(push._id_2_channels['WEBHOOK'], 'image_to_bytes', return_value=io.BytesIO(b'synthetic-image')))
            response = store.test('WEBHOOK', saved['revision'])
            assert response['sent'] == 1 and response['test_result']['success'] and len(transmissions) == 1
            assert '这是一条来自 AutoZZZ' in transmissions[-1]['data'].decode()
            checks.append('manual_test_calls_actual_webhook_transport_with_fixed_text')

            app = SimpleNamespace(ctx=ctx, app_id='email', op_name='邮件', last_screenshot=np.zeros((32, 32, 3), dtype=np.uint8))
            ctx.run_context.current_app_id = 'email'
            ctx.notify_config.set_app_notify_modes('email', 'finish_only', 'off')
            before = len(transmissions)
            send_application_notify(app, None)
            push.flush()
            assert len(transmissions) == before
            send_application_notify(app, True)
            push.flush()
            assert len(transmissions) == before + 1
            ctx.notify_config.set_app_notify_modes('email', 'start_and_finish', 'off')
            send_application_notify(app, None)
            send_application_notify(app, False)
            push.flush()
            assert len(transmissions) == before + 3
            checks.append('actual_lifecycle_hooks_off_finish_only_start_and_finish')

            @node_notify(when=NotifyTiming.CURRENT_DONE, detail=True)
            def step(operation): pass
            node = OperationNode('离线节点', op_method=step)
            success = OperationRoundResult(OperationRoundResultEnum.SUCCESS, '离线成功')
            failure = OperationRoundResult(OperationRoundResultEnum.FAIL, '离线失败')
            for detail, sent in (('off', 0), ('error_only', 1), ('all', 2), ('merge', 0)):
                ctx.run_context.notify_pool.clear()
                ctx.notify_config.set_app_notify_modes('email', 'off', detail)
                ctx.notify_config.merge_error_immediate_notify = False
                before = len(transmissions)
                send_node_notify(app, success, node)
                send_node_notify(app, failure, node)
                push.flush()
                assert len(transmissions) == before + sent, (detail, len(transmissions) - before)
                if detail == 'merge':
                    send_application_notify(app, True)
                    push.flush()
                    assert len(transmissions) == before + 1
                    assert b'---' in transmissions[-1]['data']
            ctx.notify_config.merge_error_immediate_notify = True
            ctx.run_context.notify_pool.clear()
            before = len(transmissions)
            send_node_notify(app, failure, node)
            push.flush()
            assert len(transmissions) == before + 1
            checks.append('actual_node_hooks_all_four_modes_merge_and_immediate_failure')
            assert image_conversion.call_count == 0
            push.push_config.send_image = True
            push.push('synthetic image', 'synthetic image', app.last_screenshot)
            assert image_conversion.call_count == 1
            assert b'c3ludGhldGljLWltYWdl' in transmissions[-1]['data']
            store.test('WEBHOOK', store.describe_channel('WEBHOOK')['revision'])
            assert image_conversion.call_count == 1
            push.push_config.send_image = False
            checks.append('screenshots_require_opt_in_and_manual_test_never_attaches_images')

            ctx.notify_config.enable_notify = False
            before = len(transmissions)
            assert not push.push('off', 'off')[0]
            assert len(transmissions) == before
            ctx.notify_config.enable_notify = 'false'
            assert not push.push('invalid switch', 'invalid switch')[0] and len(transmissions) == before
            ctx.notify_config.enable_notify = False
            store.test('WEBHOOK', store.describe_channel('WEBHOOK')['revision'])
            assert not ctx.notify_config.enable_notify and len(transmissions) == before + 1
            ctx.notify_config.enable_notify = True
            checks.append('manual_test_never_enables_automatic_sends')

            ntfy = store.describe_channel('NTFY')
            store.save_channel('NTFY', dict(enabled=True, URL='https://ntfy.invalid', TOPIC='fixture-topic', TOKEN='fixture-token-ntfy'), ntfy['revision'])
            ntfy_channel = push._id_2_channels['NTFY']
            stack.enter_context(patch.object(ntfy_channel, 'push', return_value=(False, 'https://ntfy.invalid/private fixture-token-ntfy')))
            ok, error = push.push('partial', 'partial')
            assert not ok and 'NTFY' in error and 'fixture-token-ntfy' not in error and 'ntfy.invalid' not in error
            checks.append('partial_delivery_is_failure_with_per_channel_redacted_results')
            d = store.describe_channel('NTFY')
            store.save_channel('NTFY', dict(enabled=False), d['revision'])
            with patch('requests.request', side_effect=requests.ConnectionError('https://autozzz-notification.invalid/fixture-token fixture-secret')):
                assert not push.push('network', 'network')[0]
            assert 'fixture-secret' not in capture.getvalue() and 'fixture-token' not in capture.getvalue()
            checks.append('transport_exceptions_and_tracebacks_do_not_expose_saved_credentials')

            # 真实操作链使用记录控制器；不绑定或操作游戏。
            service.initialize_execution('cn', GAME_PATHS['cn'])
            manager = service._execution
            wait(lambda: not manager.busy)
            assert manager.state == 'ready', manager.status()
            controller, control = ctx.controller, manager.control
            controller.bind = lambda path: None
            controller.init_before_context_run = lambda: True
            controller.probe = lambda: WindowState(True, True, (0, 0, 1920, 1080))
            controller.screenshot = lambda independent=False: control.observe(lambda: (time.time(), np.zeros((1080, 1920, 3), dtype=np.uint8)))
            stack.enter_context(patch('one_dragon.base.controller.pc_controller_base.PcControllerBase.is_game_window_ready', new_callable=PropertyMock, return_value=True))
            class Recorder:
                def press(self, key): raise AssertionError('通知不应发送游戏输入')
                def release(self, key): pass
            controller.keyboard_controller.keyboard.raw = Recorder()
            controller.keyboard_controller.mouse.raw = Recorder()
            assert ctx.run_context.start_running()
            ctx.run_context.current_app_id = 'notify'
            control.reset()
            operation = NotifyApp(ctx)
            before = len(transmissions)
            result = operation.execute()
            assert result.success, result
            assert len(transmissions) == before + 1
            assert operation.notify().is_success and len(transmissions) == before + 1
            # 复用同一缓存应用启动下一次运行时，允许新的显式通知。
            operation.execute()
            assert len(transmissions) == before + 2
            checks.append('actual_notify_operation_sends_once_per_execution_and_resets_for_new_run')
            with patch('requests.request', side_effect=requests.ConnectionError('fixture-secret')) as failed_request:
                result = operation.execute()
                assert not result.success and failed_request.call_count == 1
                assert operation.notify().is_fail and failed_request.call_count == 1
            checks.append('actual_notify_application_propagates_transport_failure_without_automatic_retry')

            gate, entered, flushed = threading.Event(), threading.Event(), threading.Event()
            def blocked(**kwargs):
                entered.set()
                assert gate.wait(5)
                return request(**kwargs)
            with patch('requests.request', side_effect=blocked):
                push.push_async('queue', 'queue')
                assert entered.wait(5)
                assert push.status()['pending'] == 1
                waiter = threading.Thread(target=lambda: (push.flush(), flushed.set()))
                waiter.start()
                assert not flushed.wait(.05)
                gate.set()
                waiter.join(5)
                assert flushed.is_set()
                assert push.status()['pending'] == 0
            checks.append('flush_waits_for_same_queued_transport_before_report_and_shutdown')
            ctx.run_context.stop_running()
            group = ctx.app_group_manager.get_one_dragon_group_config(ctx.current_instance_idx)
            for entry in group.app_list: group.set_app_enable(entry.app_id, entry.app_id == 'notify')
            manager.prepare_application('notify')
            wait(lambda: not manager.busy)
            assert manager.state == 'prepared' and not manager.uses_charge, manager.status()
            manager.prepare_application('one_dragon')
            wait(lambda: not manager.busy)
            assert manager.state == 'prepared' and manager.prepared_applications == ['notify'], manager.status()
            with patch('autozzz.windows.user32.GetAsyncKeyState', return_value=0):
                manager.start_application('one_dragon', fixed_entry=True)
                wait(lambda: not manager.busy)
            state = manager.status()
            assert state['state'] == 'success' and state['notifications']['sent'] >= 1, state
            assert state['notifications']['pending'] == 0 and state['notifications']['failed'] == 0
            checks.append('actual_preparation_and_one_dragon_run_slot_include_notify_and_wait_for_delivery')

        ctx.notify_config.enable_notify = False

        (args.data / 'ui-descriptions.json').write_text(json.dumps(dict(notify=store.describe(), channels=snapshots,
            webhook=store.describe_channel('WEBHOOK'), settings=store.describe_channel('_settings')), ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(dict(checks=checks, channel_schemas=len(channels), app_modes=len(modes),
                              external_messages_sent=False, game_accessed=False, game_input_sent=False), ensure_ascii=False))
    finally:
        log.removeHandler(handler)
        service.close()


if __name__ == '__main__': main()
