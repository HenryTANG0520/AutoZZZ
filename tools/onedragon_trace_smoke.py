"""实际 SDK 调试总线、模板匹配、Operation 与观察代次；无游戏或模型。"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import json
from pathlib import Path
import platform
import sys
import threading
import time
from types import SimpleNamespace as NS
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--data',required=True,type=Path);args=parser.parse_args()
    from autozzz.onedragon_runtime import RuntimePaths
    root=Path(__file__).resolve().parents[1];defaults=RuntimePaths.discover(root);data=args.data.resolve()
    if data.exists():raise ValueError('请使用新的隔离验证目录。')
    RuntimePaths(defaults.source,defaults.python,data).prepare();sys.path.insert(0,str(defaults.source/'src'));platform.uname()
    from autozzz.onedragon_service import OneDragonService
    from autozzz.onedragon_control import ExecutionControl,WindowState,UpstreamControlAdapter
    from one_dragon.base.debug.debug_trace_bus import DebugTraceBus,VisionTraceItem
    from one_dragon.base.matcher.template_matcher import TemplateMatcher
    from one_dragon.base.operation.operation import Operation
    from one_dragon.base.operation.operation_node import operation_node
    import numpy as np
    def forbidden(*_,**__):raise AssertionError('调试追踪回放不得访问游戏、网络或输入设备。')
    checks=[]
    with ExitStack() as scope:
        for target in ('subprocess.Popen','requests.sessions.Session.request','socket.socket.connect','socket.socket.connect_ex','socket.getaddrinfo',
                       'autozzz.windows.find_game','autozzz.windows.user32.SendInput','pynput.keyboard.Controller.press','pynput.mouse.Controller.press'):
            scope.enter_context(patch(target,side_effect=forbidden))
        service=OneDragonService(defaults.source,data);adapter=None
        try:
            ctx=service.ctx;bus=ctx.debug_trace_bus
            assert type(bus)is DebugTraceBus and not bus.enabled
            assert not service.trace_snapshot()['enabled'] and service._execution is None
            service.trace_configure(True)
            bus.set_crop_offset(40,60)
            try:bus.add_vision(VisionTraceItem('ocr','offset',1,2,11,12,.9))
            finally:bus.reset_crop_offset()
            row=service.trace_snapshot()['items']['vision'][-1];assert (row['x1'],row['y1'])==(41,62)
            checks.append('actual_source_bus_and_crop_offsets_without_controller')
            template=np.random.default_rng(42).integers(0,256,(20,30,3),dtype=np.uint8);image=np.zeros((180,320,3),dtype=np.uint8);image[50:70,70:100]=template
            loader=NS(get_template=lambda *_:NS(mask=None,get_image=lambda _:template))
            matcher=TemplateMatcher(loader,bus);result=matcher.match_template(image,'fixture','template',threshold=.95)
            assert result.max is not None
            assert any(row['source']=='template' and row['x1']==70 and row['y1']==50 for row in service.trace_snapshot()['items']['vision'])
            checks.append('actual_template_matcher_emits_recognition_trace')
            class FixtureOperation(Operation):
                @operation_node(name='fixture',is_start_node=True,screenshot_before_round=False)
                def run_fixture(self):return self.round_success('synthetic complete')
            operation=FixtureOperation(ctx,op_name='trace fixture',need_check_game_win=False)
            ctx.controller=NS(init_before_context_run=lambda:True)
            try:
                assert ctx.run_context.start_running()
                result=operation.execute();assert result.success
            finally:ctx.controller=None
            snapshot=service.trace_snapshot()
            assert snapshot['items']['decision'] and snapshot['items']['timeline']
            assert any(row['metric']=='operation_round_ms' for row in snapshot['items']['perf'])
            checks.append('actual_operation_graph_emits_decision_timeline_and_perf')
            ctx.run_context.stop_running()
            control=ExecutionControl(lambda:WindowState(True,True,(0,0,1920,1080)),lambda:None,lambda:True,lambda:None,lambda:None)
            ctx.execution_control=control;adapter=UpstreamControlAdapter(control);adapter.install();control.poll()
            service.trace_configure(True,clear=True);entered=threading.Event();release=threading.Event()
            def old_work():
                entered.set();release.wait(3);bus.add_vision(VisionTraceItem('ocr','stale',1,2,11,12,.9))
            with ThreadPoolExecutor(max_workers=1) as pool:
                with control.scope():future=pool.submit(old_work)
                assert entered.wait(2);control.set_manual_pause(True);release.set();future.result(3)
            snapshot=service.trace_snapshot();assert not snapshot['items']['vision'] and snapshot['discarded']>=1
            checks.append('actual_source_bus_rejects_old_scoped_executor_after_pause')
            control.set_manual_pause(False)
            with control.scope():bus.add_vision(VisionTraceItem('cv','fresh',1,2,11,12,.9))
            assert service.trace_snapshot()['items']['vision'][-1]['label']=='fresh'
            before=control.status();service.trace_configure(False)
            assert control.status()==before and not bus.enabled and not any(service.trace_snapshot()['items'].values())
            checks.append('disable_clears_trace_without_pausing_game_or_creating_execution')
            assert ctx.controller is None and service._execution is None
        finally:
            if adapter is not None:adapter.close()
            service.close()
    result=dict(checks=checks,actual_sdk_bus_and_producers=True,game_accessed=False,game_input_sent=False,models_loaded=False)
    (data/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
