"""Fixed SDK executor races with Event-gated fake input; never binds a game."""
from __future__ import annotations
import argparse,json,logging
from pathlib import Path
import sys,threading,time
from types import SimpleNamespace as NS

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'vendor/onedragon/src')]
from one_dragon.base.conditional_operation.operation_executor import OperationExecutor
from one_dragon.base.conditional_operation.operator import ConditionalOperator
from one_dragon.thread.atomic_int import AtomicInt
from one_dragon.utils.log_utils import log
from zzz_od.auto_battle.auto_battle_context import AutoBattleContext
from zzz_od.auto_battle.atomic_op.btn_common import AtomicBtnCommon,BtnRunStatus
from zzz_od.auto_battle.atomic_op.btn_switch_agent import AtomicBtnSwitchAgent
from zzz_od.auto_battle.atomic_op.btn_quick_assist import AtomicBtnQuickAssist
from one_dragon.base.conditional_operation.operation_def import OperationDef
from autozzz.onedragon_control import ExecutionControl,WindowState,UpstreamControlAdapter,ObservationExpired
from autozzz.onedragon_battle import BattleInputControl


def wait(predicate,timeout=5):
    deadline=time.monotonic()+timeout
    while not predicate():
        if time.monotonic()>deadline:raise AssertionError('Fixture timed out')
        time.sleep(.002)


class Capture(logging.Handler):
    def __init__(self):super().__init__();self.rows=[]
    def emit(self,record):self.rows.append(record)


class TrackedInt(AtomicInt):
    def __init__(self):super().__init__();self.history=[0];self.decrements=0
    def inc(self):super().inc();self.history.append(self.get())
    def dec(self):super().dec();self.decrements+=1;self.history.append(self.get())


def case(kind,records):
    presses=[];stops=[];entered=threading.Event();proceed=threading.Event();exited=threading.Event()
    operator=ConditionalOperator.__new__(ConditionalOperator)
    operator._task_lock=threading.Lock();operator.running_executor_cnt=TrackedInt()
    operator.running_executor=None;operator.is_running=True
    control=ExecutionControl(lambda:WindowState(True,True,(0,0,1920,1080)),lambda:stops.append('release'),
                             operator.stop_running,lambda:None,operator.stop_running)
    control.observe(lambda:'fixture')
    ctx=NS(controller=NS(keyboard_controller=NS(reset=lambda:stops.append('device_reset'))))
    adapter=BattleInputControl(ctx,control);scope=UpstreamControlAdapter(control);scope.install();adapter.install()
    adapter.allowed=True;adapter.allowed_epoch=control.epoch;adapter.permit_reason='battle_visible'
    class Gate:
        async_op=False
        def execute(self):
            entered.set()
            assert proceed.wait(5)
            try:control.input(lambda:presses.append('cancelled_press'))
            except ObservationExpired:
                if kind=='permit_restored_before_catch':
                    adapter.allowed=True;adapter.permit_reason='battle_visible'
                raise
            finally:exited.set()
        def stop(self):stops.append('gate_stop')
    class Remaining:
        async_op=False
        def execute(self):presses.append('stale_remaining')
        def stop(self):stops.append('remaining_stop')
    class PriorAsync:
        async_op=True
        def execute(self):stops.append('async_start')
        def stop(self):stops.append('async_stop')
    def submit(ops):
        operator.is_running=True;operator.running_executor_cnt.inc()
        executor=OperationExecutor(ops,time.time());operator.running_executor=executor
        with control.scope():future=executor.run_async();future.add_done_callback(operator._on_task_done)
        return executor,future
    start=len(records.rows)
    try:
        operations=[Gate(),Remaining()]
        if kind=='async_cleanup':operations.insert(0,PriorAsync())
        executor,future=submit(operations);assert entered.wait(5)
        held=False
        if kind in ('permit_revoked','async_cleanup','stop_after_retire','permit_restored_before_catch'):
            adapter.allowed=False;adapter.permit_reason='battle_not_verified'
            if kind=='stop_after_retire':operator._task_lock.acquire();held=True
        elif kind=='epoch_changed':
            with control.lock:control._invalidate()
            # A newer observation can permit a new task; this old one stays stale.
            control.observe(lambda:'new fixture');adapter.allowed_epoch=control.epoch
        elif kind=='manual_pause':control.set_manual_pause(True)
        elif kind=='battle_end':
            battle=NS(is_normal_attack_btn_available=lambda image:False,last_check_end_result=None,auto_op=operator)
            def end(*args):battle.last_check_end_result='普通战斗-完成'
            battle._check_battle_end=end
            with control.scope():assert AutoBattleContext.check_battle_state(battle,'end frame',time.time(),True)is False
            assert adapter.permit_reason=='battle_end_confirmed'and not adapter.allowed
        else:raise AssertionError(kind)
        proceed.set();wait(exited.is_set);wait(future.done)
        if held:
            # Source stop sees an already-retired sequence; its completion
            # callback is waiting for this same source accounting lock.
            operator._stop_running_task();operator._task_lock.release();held=False
        wait(lambda:operator.running_executor_cnt.get()==0)
        assert not presses,(kind,presses)
        assert stops.count('gate_stop')==1,(kind,stops)
        if kind=='async_cleanup':assert stops.count('async_stop')==1
        expected_owned=kind not in ('manual_pause','battle_end')
        assert future.result()is expected_owned,(kind,future.result())
        wait(lambda:bool(adapter.cancellations))
        cancellation=list(adapter.cancellations)[-1]
        assert cancellation['stage']=='atomic'
        assert not [row for row in records.rows[start:]if row.levelno>=logging.ERROR],kind
        # New HUD and new scoped task work normally; no scheduler count is stuck.
        if control.manual_paused:control.set_manual_pause(False)
        control.observe(lambda:'restored HUD');adapter.allowed=True;adapter.allowed_epoch=control.epoch
        adapter.permit_reason='battle_visible'
        class Fresh:
            async_op=False
            def execute(self):control.input(lambda:presses.append('fresh_press'))
            def stop(self):pass
        _,fresh=submit([Fresh()]);assert fresh.result(5)is True
        wait(lambda:operator.running_executor_cnt.get()==0)
        assert presses==['fresh_press']
        assert operator.running_executor_cnt.decrements==2 and min(operator.running_executor_cnt.history)>=0
        return dict(case=kind,owned_retirement=expected_owned,remaining_count=0,cancellation=cancellation,
                    cancelled_inputs=0,fresh_inputs=1,physical_inputs=0,no_double_decrement=True)
    finally:
        proceed.set()
        if 'held'in locals()and held:operator._task_lock.release()
        adapter.close();scope.close()


def errors_and_bounds(records):
    control=ExecutionControl(lambda:WindowState(True,True,(0,0,1920,1080)),lambda:None,lambda:None,lambda:None,lambda:None)
    control.observe(lambda:'fixture');adapter=BattleInputControl(NS(),control)
    scope=UpstreamControlAdapter(control);scope.install();adapter.install()
    adapter.allowed=True;adapter.allowed_epoch=control.epoch
    try:
        for error in (ValueError('real fixture failure'),ObservationExpired('unclassified fixture failure')):
            class Fault:
                async_op=False
                def execute(self):raise error
                def stop(self):pass
            begin=len(records.rows);future=OperationExecutor([Fault()],time.time()).run_async()
            assert future.result(5)is True
            wait(lambda:any(row.levelno>=logging.ERROR for row in records.rows[begin:]))
            assert any(row.exc_info and row.exc_info[1]is error for row in records.rows[begin:])
        adapter.allowed=False;adapter.permit_reason='battle_not_verified';begin=len(records.rows)
        for index in range(80):
            control.epoch+=1
            adapter._record_cancellation(adapter._cancellation_evidence('atomic','bounded diagnostic fixture'))
        assert len(adapter.cancellations)==16 and adapter.cancellation_count==80
        assert len([row for row in records.rows[begin:]if row.levelno==logging.INFO])==16
        return dict(real_errors_retained=True,unclassified_observation_error_retained=True,diagnostic_limit=16)
    finally:adapter.close();scope.close()


def reuse_real_atomic(kind,records):
    entered=threading.Event();proceed=threading.Event();presses=[];calls=[]
    control=ExecutionControl(lambda:WindowState(True,True,(0,0,1920,1080)),lambda:None,lambda:None,lambda:None,lambda:None)
    control.observe(lambda:'fixture');adapter=BattleInputControl(NS(),control)
    scope=UpstreamControlAdapter(control);scope.install();adapter.install()
    adapter.allowed=True;adapter.allowed_epoch=control.epoch;adapter.permit_reason='battle_visible'
    def device(*args,**kwargs):
        if kwargs.get('release'):return
        calls.append('entered')
        if len(calls)==1:entered.set();assert proceed.wait(5)
        control.input(lambda:presses.append('fake_press'))
    battle=NS(normal_attack=device,quick_assist=device,switch_by_name=device)
    cls,name={'common':(AtomicBtnCommon,'按键-普通攻击'),'switch':(AtomicBtnSwitchAgent,'按键-切换角色'),'quick':(AtomicBtnQuickAssist,'按键-快速支援')}[kind]
    atom=cls(battle,OperationDef(dict(op_name=name,agent_name='fixture')))
    begin=len(records.rows)
    try:
        with control.scope():future=OperationExecutor([atom],time.time()).run_async()
        assert entered.wait(5);assert atom._status==BtnRunStatus.RUNNING
        adapter.allowed=False;adapter.permit_reason='battle_not_verified';proceed.set();assert future.result(5)is True
        assert not presses and atom._status==BtnRunStatus.WAIT,(kind,presses,atom._status)
        # An external stop arriving after cancellation must not leave STOP.
        atom.stop();assert atom._status==BtnRunStatus.WAIT
        adapter.allowed=True;adapter.permit_reason='battle_visible'
        with control.scope():fresh=OperationExecutor([atom],time.time()).run_async()
        assert fresh.result(5)is True
        assert presses==['fake_press']and atom._status==BtnRunStatus.WAIT
        assert not [row for row in records.rows[begin:]if row.levelno>=logging.ERROR]
        return dict(atom=cls.__name__,same_instance_reused=True,status='WAIT',cancelled_inputs=0,fresh_inputs=1)
    finally:proceed.set();adapter.close();scope.close()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    records=Capture();log.addHandler(records)
    try:
        results=[case(kind,records)for kind in ('permit_revoked','epoch_changed','manual_pause','battle_end','stop_after_retire','async_cleanup','permit_restored_before_catch')]
        real_atoms=[reuse_real_atomic(kind,records)for kind in ('common','switch','quick')]
        output=dict(cases=results,real_atomic_reuse=real_atoms,**errors_and_bounds(records),sdk_source=str(ROOT/'vendor/onedragon'),game_accessed=False,physical_input=False)
        args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(output,ensure_ascii=False))
    finally:log.removeHandler(records)


if __name__=='__main__':main()
