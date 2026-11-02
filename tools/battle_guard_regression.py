"""Exercise real upstream executor accounting without binding or controlling a game."""
import json
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace

root = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(root), str(root/'vendor/onedragon/src')]
from one_dragon.base.conditional_operation.operation_executor import OperationExecutor
from one_dragon.base.conditional_operation.operator import ConditionalOperator
from one_dragon.thread.atomic_int import AtomicInt
from autozzz.onedragon_battle import BattleInputControl

control = SimpleNamespace(epoch=7, input_validator=None)
adapter = BattleInputControl(SimpleNamespace(), control)
adapter.install()
try:
    actions=[]
    class Action:
        async_op=False
        def execute(self): actions.append('normal_scene_action')
        def stop(self): pass
    counter = AtomicInt(); counter.set(1)
    operator = SimpleNamespace(_task_lock=threading.Lock(), running_executor_cnt=counter)
    from concurrent.futures import Future
    original_run=next(original for cls,name,original in adapter.originals if cls is OperationExecutor and name=='_run')
    baseline=OperationExecutor([Action()],time.time());baseline.running=True
    baseline_future=Future();baseline_future.set_result(original_run(baseline))
    ConditionalOperator._on_task_done(operator,baseline_future)
    assert actions==['normal_scene_action'] and counter.get()==0
    actions.clear();counter.inc()
    print('Unadapted source executor baseline: action completed, outstanding=0.',flush=True)
    blocked = OperationExecutor([Action()], time.time())
    future = blocked.run_async()
    result = future.result(timeout=5)
    ConditionalOperator._on_task_done(operator, future)
    output = dict(blocked_result=result, outstanding_after_rejection=counter.get(), game_input=False)
    print(json.dumps(output), flush=True)
    assert not actions, 'Guard rejection must not execute the action'
    assert counter.get()==0, 'Rejected AutoZZZ guard task leaks upstream running count and blocks normal scene'
    adapter.allowed=True;adapter.allowed_epoch=control.epoch
    counter.inc()
    valid=OperationExecutor([Action()], time.time())
    future=valid.run_async();future.result(timeout=5)
    ConditionalOperator._on_task_done(operator,future)
    assert actions==['normal_scene_action'] and counter.get()==0
    adapter.allowed=False
    cancelled=OperationExecutor([],time.time())
    cancelled.running=True
    counter.inc()
    assert cancelled.stop() is False
    counter.dec()  # source _stop_running_task owns cancellation accounting
    future=Future();future.set_result(cancelled._run())
    ConditionalOperator._on_task_done(operator,future)
    assert counter.get()==0, 'Already-stopped worker must not decrement twice'
    adapter.allowed=True;adapter.allowed_epoch=control.epoch-1
    counter.inc()
    stale=OperationExecutor([Action()],time.time())
    future=stale.run_async();future.result(timeout=5)
    ConditionalOperator._on_task_done(operator,future)
    assert counter.get()==0 and len(actions)==1, 'Stale epoch must retire without sending an action'
    print('Fresh-frame task executes; accounting returns to zero; no physical input.',flush=True)
finally:
    adapter.close()
