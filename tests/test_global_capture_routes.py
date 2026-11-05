import queue
import threading
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock,patch

from autozzz.global_capture_ui import GlobalCaptureController


class Client:
    instances=[]
    def __init__(self,paths):
        self.paths=paths;self.alive=True;self.calls=[];self.close_gate=None;self.instances.append(self)
    def start(self):pass
    def submit(self,method,**params):self.calls.append((method,params));return len(self.calls)
    def result(self,key,timeout):return dict(method=self.calls[key-1][0],path='owned.png')
    def close(self,timeout=1):
        if self.close_gate and not self.close_gate.is_set():
            from autozzz.onedragon_runtime import RequestPending
            raise RequestPending('close')
        self.alive=False


class GlobalCaptureRoutingTests(unittest.TestCase):
    def setUp(self):
        self.ui=NS(root=NS(),server=NS(get=lambda:'国服'),game_path=NS(get=lambda:'cn.exe'),_log=Mock(),running=False,runner=None)
        Client.instances=[]
        self.patches=[patch('autozzz.global_capture_ui.OneDragonClient',Client),patch('autozzz.global_capture_ui.RuntimePaths.discover',side_effect=lambda root,profile:profile)]
        for item in self.patches:item.start();self.addCleanup(item.stop)
        self.controller=GlobalCaptureController(self.ui,hotkeys=False);self.addCleanup(self.close)

    def close(self):
        for client in Client.instances:
            if client.close_gate:client.close_gate.set()
        self.controller.close();self.controller.worker.join(3);self.assertFalse(self.controller.worker.is_alive())

    def wait(self,predicate):
        deadline=time.monotonic()+3
        while not predicate():
            self.controller.poll()
            if time.monotonic()>deadline:self.fail('capture routing did not settle')
            time.sleep(.005)

    def test_idle_uses_one_readonly_connection_and_reuses_it(self):
        done=[]
        self.controller.request('global_capture_status',{},lambda **value:done.append(value));self.wait(lambda:len(done)==1)
        self.controller.request('global_capture_capture',dict(token='token'),lambda **value:done.append(value));self.wait(lambda:len(done)==2)
        self.assertEqual(len(Client.instances),1)
        self.assertEqual([method for method,_ in Client.instances[0].calls],['global_capture_status','global_capture_capture'])

    def test_native_routes_original_session_and_close_never_stops_it(self):
        session=NS(send=Mock(),stop=Mock(),close=Mock())
        self.ui.execution_window=NS(closed=False,closing=False,connected=True,profile='global',game_path=NS(get=lambda:'global.exe'),
            app_id='charge_plan',worker=NS(is_alive=lambda:True),session=session)
        done=[];self.controller.request('global_capture_capture',dict(token='token'),lambda **value:done.append(value))
        params=session.send.call_args.kwargs
        self.assertEqual(params['params']['game_path'],'global.exe');self.assertFalse(Client.instances)
        self.controller.close();self.assertTrue(done[0]['error']);session.stop.assert_not_called();session.close.assert_not_called()
        self.controller.receive(dict(type='rpc_result',tag=params['tag'],result={'path':'late.png'}));self.assertEqual(len(done),1)

    def test_legacy_routes_revocable_capability_without_stopping_runner(self):
        lease=NS(export=Mock(return_value='readonly-descriptor'),close=Mock())
        runner=NS(settings=NS(server='cn',game_path='legacy.exe'),capture_lease=Mock(return_value=lease),stop=Mock())
        self.ui.runner=runner;self.ui.running=True;done=[]
        self.controller.request('global_capture_capture',dict(token='token'),lambda **value:done.append(value));self.wait(lambda:bool(done))
        params=Client.instances[0].calls[0][1]
        self.assertEqual(params['legacy_lease'],'readonly-descriptor');self.assertEqual(params['game_path'],'legacy.exe')
        lease.close.assert_called_once();runner.stop.assert_not_called()

    def test_rotation_borrows_existing_client_without_closing_it(self):
        borrowed=Client('original');original_close=Mock();borrowed.close=original_close
        self.ui.execution_window=NS(closed=False,closing=False,connected=True,profile='cn',game_path=NS(get=lambda:'cn.exe'),
            app_id='_rotation',worker=NS(is_alive=lambda:True),session=NS(client=borrowed,bound=True))
        done=[];self.controller.request('global_capture_status',{},lambda **value:done.append(value));self.wait(lambda:bool(done))
        self.assertEqual(len(Client.instances),1);self.assertIsNone(borrowed.calls[0][1]['game_path'])
        self.controller.close();self.controller.worker.join(3);original_close.assert_not_called()

    def test_active_legacy_takes_priority_over_an_open_completed_native_window(self):
        session=NS(send=Mock(),stop=Mock(),close=Mock())
        self.ui.execution_window=NS(closed=False,closing=False,connected=True,busy=False,profile='global',
            game_path=NS(get=lambda:'idle-global.exe'),app_id='coffee',worker=NS(is_alive=lambda:True),session=session)
        lease=NS(export=Mock(return_value='active-legacy-capability'),close=Mock())
        self.ui.runner=NS(settings=NS(server='cn',game_path='active-cn.exe'),capture_lease=Mock(return_value=lease),stop=Mock())
        self.ui.running=True;done=[]
        self.controller.request('global_capture_capture',dict(token='token'),lambda **value:done.append(value))
        self.wait(lambda:bool(done));session.send.assert_not_called()
        params=Client.instances[0].calls[0][1]
        self.assertEqual(params['legacy_lease'],'active-legacy-capability')
        self.assertEqual(params['game_path'],'active-cn.exe');lease.close.assert_called_once()
        self.ui.runner.stop.assert_not_called()

    def test_close_retains_worker_until_original_readonly_process_exits(self):
        done=[];self.controller.request('global_capture_status',{},lambda **value:done.append(value));self.wait(lambda:bool(done))
        gate=threading.Event();Client.instances[0].close_gate=gate
        self.controller.close();self.controller.worker.join(.03)
        self.assertTrue(self.controller.worker.is_alive());self.assertFalse(self.controller.closed)
        gate.set();self.controller.worker.join(3);self.controller.poll();self.assertTrue(self.controller.closed)

    def test_native_tagged_error_does_not_change_original_execution_busy(self):
        from autozzz.onedragon_run_ui import ExecutionWindow
        events=queue.Queue();event=dict(type='error',tag='global-capture-fixture',message='screenshot refused');events.put(event)
        receive=Mock()
        window=NS(closed=False,closing=False,events=events,busy=True,poll=lambda:None,
            ui=NS(global_capture_controller=NS(receive=receive)),update_buttons=lambda:None,window=NS(after=lambda *_:None))
        ExecutionWindow.poll(window)
        self.assertTrue(window.busy);receive.assert_called_once_with(event)

    def test_main_close_waits_for_readonly_sdk_exit(self):
        from autozzz.gui import AutoZZZWindow
        class Quiet(AutoZZZWindow):
            @property
            def dirty(self):return False
        ui=Quiet.__new__(Quiet);ui.closing=ui.busy=ui.running=False;ui.worker=ui.pip_controller=None
        ui.root=Mock();ui.startup_updates=NS(worker_alive=False,close=Mock());ui._destroy_root=Mock()
        ui.global_capture_controller=self.controller
        done=[];self.controller.request('global_capture_status',{},lambda **value:done.append(value));self.wait(lambda:bool(done))
        gate=threading.Event();Client.instances[0].close_gate=gate
        ui._close();self.controller.worker.join(.03)
        self.assertTrue(ui._maintenance_workers_alive());ui._destroy_root.assert_not_called();ui.root.withdraw.assert_called_once()
        gate.set();self.controller.worker.join(3);self.assertFalse(ui._maintenance_workers_alive())

    def test_runtime_generation_change_reopens_only_after_original_exits(self):
        generation=['first'];done=[]
        with patch('autozzz.global_capture_ui.RuntimePaths.discover',side_effect=lambda *args:NS(generation=generation[0])),\
                patch.object(GlobalCaptureController,'current',side_effect=lambda paths:paths.generation==generation[0]):
            self.controller.request('global_capture_status',{},lambda **value:done.append(value));self.wait(lambda:len(done)==1)
            old=Client.instances[0];generation[0]='second'
            self.controller.request('global_capture_status',{},lambda **value:done.append(value));self.wait(lambda:len(done)==2)
            self.assertFalse(old.alive);self.assertEqual(len(Client.instances),2)
            self.assertEqual(Client.instances[1].paths.generation,'second')


if __name__=='__main__':unittest.main()
