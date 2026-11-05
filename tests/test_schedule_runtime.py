import ctypes as C
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock,patch
import uuid
import contextlib
import io

from autozzz.config import Settings
from autozzz.schedule_authorization import authorize,daily_review
from autozzz.schedule_runtime import desktop_available,execute,run_scheduled
from autozzz.schedules import Schedule,ScheduleStore,revision


class DesktopAvailabilityTests(unittest.TestCase):
    def test_requires_active_session_and_default_input_desktop(self):
        for desktop, state, size, queried, expected in (
            ('Default', 0, 4, True, True),
            ('Default', 4, 4, True, False),
            ('Winlogon', 0, 4, True, False),
            ('Default', 0, 2, True, False),
            ('Default', 0, 0, False, False),
        ):
            with self.subTest(desktop=desktop, state=state, size=size, queried=queried):
                session_status = C.c_int(state)

                def query(server, session, info, output, output_size):
                    self.assertEqual((server, session, info), (None, 0xffffffff, 8))
                    if queried:
                        state_pointer = C.cast(output, C.POINTER(C.c_void_p))
                        state_pointer.contents.value = C.addressof(session_status)
                        output_size._obj.value = size
                    return queried

                def name(_handle, _index, buffer, _size, _needed):
                    buffer.value = desktop
                    return True

                user32 = NS(OpenInputDesktop=Mock(return_value=100),
                                         GetUserObjectInformationW=Mock(side_effect=name), CloseDesktop=Mock())
                wtsapi32 = NS(WTSQuerySessionInformationW=Mock(side_effect=query), WTSFreeMemory=Mock())
                with patch.object(C, 'WinDLL', side_effect=lambda name, **_: {'user32': user32, 'wtsapi32': wtsapi32}[name]):
                    self.assertEqual(desktop_available(), expected)
                user32.CloseDesktop.assert_called_once_with(100)
                if wtsapi32.WTSQuerySessionInformationW.called and queried:
                    wtsapi32.WTSFreeMemory.assert_called_once()
                else:
                    wtsapi32.WTSFreeMemory.assert_not_called()


class ScheduledRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        game=self.root/'ZenlessZoneZero.exe';game.touch();Settings(game_path=str(game)).save(self.root/'daily.json')
        self.plan=Schedule();self.plan.authorization=authorize(self.plan,daily_review(self.plan,self.root/'daily.json'));self.plan.enabled=True
        self.store=ScheduleStore(self.root,self.root/'schedules/plans.json');self.store.put(self.plan,revision(self.store.read()))
        self.instance=str(uuid.uuid4());self.matches=True
        def call(method,**params):
            if method=='list':return dict(tasks=[dict(id=self.plan.id)])
            if method=='instances':return dict(instances=[dict(instance=self.instance)])
            raise AssertionError(method)
        self.manager=NS(backend=NS(call=call),matches=lambda *_:self.matches)

    def run_job(self,execute=None,available=True):
        return run_scheduled(self.plan.id,self.store.path,self.plan.fingerprint,root=self.root,
            manager_factory=lambda _:self.manager,execute_task=execute or Mock(return_value=dict(state='success',report='fixture')),
            check_desktop=lambda:available)

    def test_same_windows_instance_is_claimed_once_and_original_record_is_preserved(self):
        execute=Mock(return_value=dict(state='success',report='fixture'));result=self.run_job(execute)
        path=Path(result['record']);before=path.read_bytes()
        self.assertEqual(json.loads(before)['state'],'success');self.assertEqual(self.run_job(execute)['state'],'duplicate')
        self.assertEqual(execute.call_count,1);self.assertEqual(path.read_bytes(),before)

    def test_stale_disabled_or_changed_system_task_never_starts_executor(self):
        execute=Mock();self.matches=False
        with self.assertRaises(ValueError):self.run_job(execute)
        self.matches=True;self.store.put(replace(self.plan,enabled=False),revision(self.store.read()))
        with self.assertRaises(ValueError):self.run_job(execute)
        execute.assert_not_called();self.assertFalse((self.store.path.parent/'runs').exists())

    def test_unavailable_desktop_is_recorded_without_starting_game(self):
        execute=Mock();result=self.run_job(execute,False);execute.assert_not_called()
        self.assertEqual(result['state'],'skipped');self.assertIn('未启动游戏',json.loads(Path(result['record']).read_text(encoding='utf-8'))['error'])

    def test_stop_file_waits_for_original_executor_cleanup_before_terminal_record(self):
        started=threading.Event();released=threading.Event();observed=threading.Event()
        folder=self.store.path.parent/'runs'/self.plan.id/uuid.UUID(self.instance).hex
        def stop_later():
            if not started.wait(2):return
            (folder/'stop').touch();observed.set()
            while json.loads((folder/'record.json').read_text())['state']!='running':time.sleep(.01)
            time.sleep(.15);self.assertNotIn('finished_at',json.loads((folder/'record.json').read_text()));released.set()
        helper=threading.Thread(target=stop_later);helper.start()
        def execute(plan,review,stop,emit,report_root):
            started.set()
            self.assertTrue(stop.wait(2));self.assertTrue(released.wait(2));return dict(state='stopped',report='original')
        try:self.assertEqual(self.run_job(execute)['state'],'stopped')
        finally:helper.join(3)
        self.assertTrue(observed.is_set());self.assertFalse(helper.is_alive())

    def test_stop_file_written_during_admission_prevents_executor_start(self):
        def desktop_check():
            path=next((self.store.path.parent/'runs').glob('*/*/record.json'))
            path.with_name('stop').touch()
            return True
        executor=Mock()
        result=run_scheduled(self.plan.id,self.store.path,self.plan.fingerprint,root=self.root,
            manager_factory=lambda _:self.manager,execute_task=executor,check_desktop=desktop_check)
        self.assertEqual(result['state'],'stopped')
        executor.assert_not_called()

    def test_stop_seen_while_delivering_prepared_event_is_not_dispatched_as_start(self):
        stop=threading.Event()
        commands=[]
        class Session:
            def __init__(self,_paths,emit,*,report_root,stop_event):
                self.worker=NS(join=lambda:None)
                self.plan=dict(instances=[])
                self.emit=emit
                emit(dict(type='connected'))
            def send(self,command,**params):
                commands.append(command)
                state='prepared' if command=='prepare' else 'success'
                self.emit(dict(type='state',state=dict(state=state,busy=False,uses_charge=False,
                                                     resources=dict(confirmation_required=False))))
            def close(self):pass
        def on_event(event):
            if event.get('state',{}).get('state')=='prepared':
                stop.set()
        with (patch('autozzz.onedragon_rotation_session.RotationSession',Session),
              patch('autozzz.onedragon_runtime.RuntimePaths.discover')):
            result=execute(Schedule(mode='native',instance=1),dict(intent='fixture',uses_charge=False,instances=[]),
                           stop,on_event,self.root)
        self.assertEqual(result['state'],'stopped')
        self.assertEqual(commands,['prepare'])

    def test_executor_error_is_recorded_without_restart(self):
        execute=Mock(side_effect=RuntimeError('original failure'))
        with self.assertRaisesRegex(RuntimeError,'original failure'):self.run_job(execute)
        path=next((self.store.path.parent/'runs').rglob('record.json'));value=json.loads(path.read_text())
        self.assertEqual(value['state'],'failed');self.assertEqual(execute.call_count,1)

    def test_live_record_tracks_focus_wait_without_a_report_path_as_status(self):
        def execute(plan, review, stop, emit, report_root):
            record = report_root.parent / 'record.json'

            def phase():
                return json.loads(record.read_text(encoding='utf-8'))['phase']

            emit(dict(type='state', state=dict(busy=True, preparation_step='登录', control=dict(focus_waiting=True))))
            self.assertEqual(phase(), '等待游戏恢复焦点')
            emit(dict(type='report', path='some/report.json'))
            self.assertEqual(phase(), '等待游戏恢复焦点')
            emit(dict(type='state', state=dict(busy=True, preparation_step='登录', control={})))
            self.assertEqual(phase(), '登录')
            emit(dict(type='focus_wait', waiting=True))
            self.assertEqual(phase(), '等待游戏恢复焦点')
            emit(dict(type='paused', paused=True))
            emit(dict(type='focus_wait', waiting=False))
            self.assertEqual(phase(), '已暂停')
            return dict(state='stopped', report='original')

        self.assertEqual(self.run_job(execute)['state'], 'stopped')

    def test_legacy_partial_daily_result_keeps_verified_daily_and_original_details(self):
        result=self.run_job(Mock(return_value=dict(state='partial',report=dict(status='partial',daily_verified=True,results=[dict(task='weekly',status='partial')]))))
        value=json.loads(Path(result['record']).read_text())
        self.assertEqual(value['state'],'partial');self.assertTrue(value['result']['report']['daily_verified'])
        self.assertEqual(value['result']['report']['results'][0]['status'],'partial')

    def test_scheduled_cli_rejects_incomplete_or_overriding_arguments_before_execution(self):
        from autozzz.__main__ import main
        valid=['--scheduled-run',self.plan.id,'--schedule-store',str(self.store.path),'--schedule-revision',self.plan.fingerprint]
        for args in (['--scheduled-run',self.plan.id],['--schedule-store',str(self.store.path)],valid+['--server','cn'],valid+['--energy-limit','20']):
            with self.subTest(args=args),contextlib.redirect_stderr(io.StringIO()),patch('autozzz.schedule_runtime.run_scheduled') as execute,self.assertRaises(SystemExit):
                main(args)
            execute.assert_not_called()

    def test_scheduled_cli_forwards_original_binding_once(self):
        from autozzz.__main__ import main
        args=['--scheduled-run',self.plan.id,'--schedule-store',str(self.store.path),'--schedule-revision',self.plan.fingerprint]
        with contextlib.redirect_stdout(io.StringIO()),patch('autozzz.schedule_runtime.run_scheduled',return_value=dict(state='success')) as execute:
            self.assertEqual(main(args),0)
        execute.assert_called_once_with(self.plan.id,self.store.path,self.plan.fingerprint)


if __name__=='__main__':unittest.main()
