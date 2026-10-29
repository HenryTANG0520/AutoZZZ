"""只读采样实际上游执行器与状态记录，不参与战斗判断。"""
from __future__ import annotations

import math
import time


def battle_snapshot(ctx,now=None):
    now=time.time() if now is None else now
    result=dict(available=False,running=False,policy='',states=[],task=None,truncated=False)
    try:
        battle=ctx.__dict__.get('auto_battle_context')
        operator=None if battle is None else battle.auto_op
        if operator is None:return result
        result.update(available=True,policy=str(operator.get_template_name())[:256])
        if not operator.is_running:return result
        result['running']=True
        executor=operator.running_executor;info=operator.current_execution_info
        if executor is not None and info is not None:
            elapsed=now-executor.trigger_time
            if math.isfinite(elapsed):
                result['task']=dict(trigger=str(info.trigger_display)[:2048],conditions=str(info.expr_display)[:16384],
                                    priority=str(info.priority_display)[:64],elapsed=round(max(0,elapsed),3))
        # 与上游状态面板一致：仅展示该策略使用且已触发的记录；已清除的角色槽不展示。
        for name in sorted(operator.usage_states):
            recorder=battle.state_record_service.get_state_recorder(name)
            if recorder is None:continue
            stamp,value=recorder.last_record_time,recorder.last_value
            if not isinstance(stamp,(int,float)) or not math.isfinite(stamp) or stamp<0:continue
            if stamp==0 and name.startswith(('前台-','后台-')):continue
            if len(result['states'])==1024:result['truncated']=True;break
            if value is not None and (type(value) not in (int,float) or not math.isfinite(value)):value=None
            result['states'].append(dict(name=name[:256],age=round(min(999,max(0,now-stamp)),3),value=value))
        # 切换策略或停止时不把旧执行器的状态留在面板上。
        if battle.auto_op is not operator or not operator.is_running:
            result.update(running=False,states=[],task=None)
    except (AttributeError,RuntimeError,TypeError,ValueError,OverflowError):
        # 状态监视不能让运行状态查询失败，下一次查询重新读取。
        result.update(available=False,running=False,states=[],task=None)
    return result
