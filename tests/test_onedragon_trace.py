from collections import deque
from contextvars import ContextVar
import json
import threading
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock,patch

from autozzz.onedragon_control import ExecutionControl,WindowState
from autozzz.onedragon_trace import TraceRuntime,KINDS,plain_debug
from autozzz.onedragon_trace_session import TraceSubscription


class Bus:
    def __init__(self):
        self.enabled=True;self.lock=threading.RLock();self.items={kind:deque(maxlen=2500) for kind in KINDS}
        for kind in KINDS:setattr(self,'add_'+kind,lambda item,kind=kind:self.items[kind].append(item))
    def clear(self):
        for rows in self.items.values():rows.clear()
    def snapshot(self):return NS(**{kind+'_items':list(rows) for kind,rows in self.items.items()})


def item(kind,**values):
    common=dict(created=time.time(),meta={})
    common.update({'vision':dict(source='ocr',label='400',x1=10,y1=20,x2=90,y2=60,score=.99),
                   'decision':dict(source='AutoBattleOperator',trigger='闪避识别',expression='[闪避识别-黄光]',operation='按键-闪避',status='执行'),
                   'timeline':dict(category='operation',title='操作开始',detail='节点',level='INFO'),
                   'perf':dict(metric='ocr_ms',value=12.5,unit='ms')}[kind]);common.update(values);return NS(**common)


class TraceTests(unittest.TestCase):
    def setUp(self):
        self.control=ExecutionControl(lambda:WindowState(True,True,(0,0,1920,1080)),lambda:None,lambda:True,lambda:None,lambda:None)
        self.bus=Bus();self.ctx=NS(debug_trace_bus=self.bus,execution_control=self.control,controller=None)
        self.trace=TraceRuntime(self.ctx);self.addCleanup(self.trace.close)

    def test_disabled_by_default_and_original_bus_restored_on_close(self):
        self.bus.add_vision(item('vision'));self.assertFalse(self.bus.enabled)
        self.assertFalse(self.trace.snapshot()['items']['vision'])
        self.trace.close();self.bus.add_vision(item('vision'));self.assertEqual(len(self.bus.items['vision']),1)
        self.assertFalse(self.bus.enabled)

    def test_pause_epoch_discards_late_async_records_and_clears_previous_picture(self):
        self.trace.configure(True)
        with self.control.scope():
            self.bus.add_vision(item('vision'));self.assertTrue(self.trace.snapshot()['items']['vision'])
            self.control.set_manual_pause(True);self.bus.add_vision(item('vision',label='stale'))
        snapshot=self.trace.snapshot();self.assertFalse(snapshot['items']['vision']);self.assertEqual(snapshot['discarded'],1)
        self.control.set_manual_pause(False)
        with self.control.scope():self.bus.add_vision(item('vision',label='new'))
        self.assertEqual(self.trace.snapshot()['items']['vision'][0]['label'],'new')

    def test_same_epoch_collects_four_kinds_and_reports_truncation_within_protocol_size(self):
        self.trace.configure(True)
        for kind in KINDS:
            for index in range(1600):getattr(self.bus,'add_'+kind)(item(kind,meta=dict(long='屏幕文字'*2000,index=index)))
        result=self.trace.snapshot()
        self.assertTrue(all(result['truncated'].values()));self.assertEqual(set(result['items']),set(KINDS))
        self.assertLess(len(json.dumps(result,ensure_ascii=False,allow_nan=False).encode('utf-8')),1_000_000)
        self.assertTrue(all(rows[-1]['meta']['index']==1599 for rows in result['items'].values()))

    def test_source_ttl_and_invalid_data_are_filtered(self):
        self.trace.configure(True);now=time.time()
        self.bus.add_vision(item('vision',created=now-2));self.bus.add_perf(item('perf',value=float('nan')))
        self.bus.add_decision(item('decision',created=10**1000));self.bus.add_timeline(item('timeline',created=now-61))
        self.bus.add_vision(item('vision',x2=0));self.bus.add_perf(item('perf',metric='valid'))
        result=self.trace.snapshot(now);self.assertEqual(len(result['items']['perf']),1)
        self.assertFalse(any(result['items'][kind] for kind in ('vision','decision','timeline')))

    def test_clear_and_disable_change_generation_and_keep_game_control_unchanged(self):
        self.trace.configure(True);self.bus.add_decision(item('decision'));before=self.control.status()
        old=self.trace.generation;self.trace.configure(True,clear=True);self.assertGreater(self.trace.generation,old)
        self.assertFalse(self.trace.snapshot()['items']['decision']);self.trace.configure(False)
        self.assertEqual(self.control.status(),before)
        for value in ('true',1,None):
            with self.assertRaises(ValueError):self.trace.configure(value)

    def test_boss_timing_hooks_are_explicit_and_restored_on_disable(self):
        from autozzz.onedragon_boss_timing import BossTimingTrace

        original=Mock()
        guard=NS(_observe_hud=original)
        execution=NS(ctx=self.ctx,control=self.control,battle_control=guard)
        timing=BossTimingTrace.__new__(BossTimingTrace)
        timing.hooks=[]
        timing.install=lambda:timing._hook(guard,'_observe_hud',timing._hud)
        with patch('autozzz.onedragon_boss_timing.BossTimingTrace',return_value=timing) as create:
            regular=self.trace.configure(True)
            create.assert_not_called()
            self.assertIs(guard._observe_hud,original)
            enabled=self.trace.configure(True,boss_timing=True,execution=execution)
            self.assertIsNot(guard._observe_hud,original)
            self.assertGreater(enabled['generation'],regular['generation'])
            self.assertTrue(self.trace.configure(True)['enabled'])
            self.assertIs(guard._observe_hud,original)
            self.bus.add_timeline(item('timeline'))
            self.assertEqual(len(self.trace.snapshot()['items']['timeline']),1)
            self.trace.configure(True,boss_timing=True,execution=execution)
            self.assertFalse(self.trace.configure(False)['enabled'])
            self.assertIs(guard._observe_hud,original)
            self.assertFalse(timing.hooks)

    def test_epoch_change_during_serialization_discards_entire_old_snapshot(self):
        self.trace.configure(True);self.bus.add_vision(item('vision'));original=self.trace._record
        def record(*args):
            result=original(*args);self.control.set_manual_pause(True);return result
        with patch.object(self.trace,'_record',side_effect=record):result=self.trace.snapshot()
        self.assertFalse(any(result['items'].values()));self.assertEqual(result['epoch'],self.control.epoch)

    def test_metadata_is_bounded_without_object_repr_or_nonfinite_numbers(self):
        class Unknown:
            def __repr__(self):raise AssertionError('repr must not run')
        data={};data['recursive']=data;data['unknown']=Unknown();data['huge']=10**1000;data['text']='x\ud800y';data['nan']=float('nan')
        result=plain_debug(data);serialized=json.dumps(result,ensure_ascii=False,allow_nan=False).encode('utf-8')
        self.assertLess(len(serialized),1000);self.assertIsNone(result['huge']);self.assertEqual(result['unknown'],'<Unknown>')

    def test_long_expressions_are_explicitly_marked_as_truncated(self):
        self.trace.configure(True);self.bus.add_decision(item('decision',expression='条件'*10000))
        row=self.trace.snapshot()['items']['decision'][0]
        self.assertEqual(row['truncated_fields'],['expression']);self.assertTrue(row['expression'].endswith('…'))

    def test_invalid_appearance_cannot_prevent_turning_overlay_off(self):
        from autozzz.onedragon_trace_ui import TraceWindow
        hidden=[];view=NS(overlay=NS(enabled=True,hide=lambda:hidden.append(True)),cover=NS(get=lambda:False),
                          font_size=NS(get=lambda:'invalid'),opacity=NS(get=lambda:'85'),layers={},sources={},status=NS(set=lambda _:None))
        self.assertFalse(TraceWindow.overlay_options(view));self.assertFalse(view.overlay.enabled);self.assertEqual(hidden,[True])


class BossTimingFailureTests(unittest.TestCase):
    class Incomplete:
        def __init__(self,**values):
            self.__dict__.update(values)
            self.failed_reads=[]

        def __getattr__(self,name):
            self.failed_reads.append(name)
            raise RuntimeError('Diagnostic metadata is no longer available')

    def setUp(self):
        from autozzz.onedragon_boss_timing import BossTimingTrace

        self.timing=BossTimingTrace.__new__(BossTimingTrace)
        for name in ('round','task','atom'):
            setattr(self.timing,name,ContextVar(name,default=None))

    def assert_native_unchanged(self,hook,args,result):
        native=Mock(return_value=result)
        self.assertIs(hook(native,*args),result)
        native.assert_called_once_with(*args)
        failure=ValueError('Native error')
        native=Mock(side_effect=failure)
        with self.assertRaises(ValueError) as raised:
            hook(native,*args)
        self.assertIs(raised.exception,failure)
        native.assert_called_once_with(*args)

    def test_diagnostic_metadata_failures_preserve_each_native_call(self):
        timing=self.timing
        result,screen=object(),object()
        with self.subTest(hook='round'):
            incomplete=self.Incomplete()
            self.assert_native_unchanged(timing._round,(incomplete,),result)
            self.assertEqual(incomplete.failed_reads,['_current_node']*2)

        with self.subTest(hook='hud'):
            operation=NS(last_screenshot=screen,last_screenshot_time=1)
            timing.round.set(NS(operation=lambda:operation,thread=threading.get_ident(),origin=None))
            hud=self.Incomplete()
            self.assert_native_unchanged(timing._hud,(object(),screen,1),hud)
            self.assertEqual(hud.failed_reads,['normal_available'])

        button=self.Incomplete()
        executor=NS(_current_op=button,op_list=[button])
        with self.subTest(hook='submit'):
            info=self.Incomplete(op_list=executor.op_list,trigger='按键禁用-终结技')
            operator=NS(current_execution_info=info,running_executor=executor)
            timing.runtime=NS(ctx=NS(auto_battle_context=NS(auto_op=operator)))
            timing.round.set(NS(origin=(1,1,{})))
            self.assert_native_unchanged(timing._submit,(executor,),result)
            self.assertEqual(info.failed_reads,['state_list']*2)
            self.assertIsNone(timing.task.get())

        with self.subTest(hook='atomic'):
            timing.task.set(NS(executor=executor,number=1,origin=(1,1,{})))
            timing.atom_kinds={type(button):'button'}
            self.assert_native_unchanged(timing._atomic,(button,),result)
            self.assertEqual(button.failed_reads,['btn_name']*2)
            self.assertIsNone(timing.atom.get())

    def test_timing_clock_failure_preserves_the_existing_input_callback(self):
        self.timing.atom.set((NS(origin=(1,1,{}),number=1),0))
        result=object()
        action=Mock(return_value=result)
        guarded_input=Mock(side_effect=lambda callback:callback())
        with patch('autozzz.onedragon_boss_timing.perf_counter',side_effect=RuntimeError('Clock unavailable')):
            self.assertIs(self.timing._input(guarded_input,action),result)
        guarded_input.assert_called_once()
        action.assert_called_once_with()


class SubscriptionTests(unittest.TestCase):
    def setUp(self):
        self.events=[];self.subscription=TraceSubscription(lambda event:self.events.append(self.subscription.take() if event['type']=='trace' else event));self.calls=[];self.client=object()
    def call(self,method,**params):self.calls.append((method,params));return dict(enabled=True,items={},private_screen_text='fixture')

    def test_disabled_subscription_never_queries_or_initializes_context(self):
        self.subscription.pump(self.client,self.call);self.assertFalse(self.calls)

    def test_reuses_connection_and_resubscribes_only_after_confirmed_replacement(self):
        self.subscription.configure(True);self.subscription.pump(self.client,self.call);self.subscription.next_poll=0
        self.subscription.pump(self.client,self.call);self.assertEqual(sum(method=='trace_configure' for method,_ in self.calls),1)
        self.subscription.disconnected();replacement=object();self.subscription.pump(replacement,self.call)
        self.assertEqual(sum(method=='trace_configure' for method,_ in self.calls),2)
        self.assertTrue(any(event['type']=='trace' and event['snapshot']is None for event in self.events))

    def test_late_snapshot_cannot_undo_new_disable_request(self):
        self.subscription.configure(True)
        def call(method,**params):
            result=self.call(method,**params)
            if method=='trace_snapshot':self.subscription.configure(False)
            return result
        self.subscription.pump(self.client,call);self.assertFalse(self.events)
        self.subscription.pump(self.client,self.call)
        self.assertIsNone(self.events[-1]['snapshot']);self.assertEqual(self.calls[-1],('trace_configure',dict(enabled=False,clear=False)))

    def test_clear_is_submitted_once_and_errors_are_separate_from_game_state(self):
        self.subscription.configure(True,clear=True);self.subscription.pump(self.client,self.call);self.subscription.next_poll=0
        self.subscription.pump(self.client,self.call);self.assertEqual(sum(params.get('clear',False) for _,params in self.calls),1)
        self.subscription.next_poll=0
        def failed(*_,**__):raise RuntimeError('fixture trace unavailable')
        self.subscription.pump(self.client,failed);self.assertEqual(self.events[-1]['type'],'trace_error')
        self.assertFalse(any(event['type']=='state' for event in self.events))

    def test_disable_is_acknowledged_once_without_continuing_snapshot_polls(self):
        self.subscription.configure(False);self.subscription.pump(self.client,self.call)
        count=len(self.events);self.subscription.next_poll=0;self.subscription.pump(self.client,self.call)
        self.assertEqual(len(self.events),count);self.assertEqual(self.calls,[('trace_configure',dict(enabled=False,clear=False))])

    def test_late_error_cannot_overwrite_new_view_intent(self):
        self.subscription.configure(True)
        def failed(*_,**__):self.subscription.configure(False);raise RuntimeError('old error')
        self.subscription.pump(self.client,failed);self.assertFalse(self.events)

    def test_busy_ui_retains_one_notification_and_only_latest_snapshot(self):
        notices=[];subscription=TraceSubscription(notices.append);subscription.configure(True)
        for _ in range(100):subscription.next_poll=0;subscription.pump(self.client,self.call)
        self.assertEqual(notices,[dict(type='trace')]);self.assertIsNotNone(subscription.take()['snapshot'])
        self.assertIsNone(subscription.take());subscription.disconnected();self.assertEqual(len(notices),2)


if __name__=='__main__':unittest.main()
