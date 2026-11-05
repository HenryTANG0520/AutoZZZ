"""Wait for one original task without holding the service dispatch lock."""
from concurrent.futures import Future
import copy
import json
import threading


class PendingTool:
    def __init__(self,execution=None,job_id=None,kind=None,record=None,enter=True):
        self.execution,self.job_id,self.kind,self.record,self.enter=execution,job_id,kind,record,enter
        self.worker=None if execution is None else execution.worker

    def result(self):
        if self.record is not None:
            state=self.record['future'].result()
            success=state['state']=='success'
            if self.kind in ('click_game','key_tap','drag','input_text'):
                output=dict(self.record['output']or{})
                if not output:output=dict(success=False,error=state.get('error')or'任务已停止，未完成输入。')
                return output
            if self.kind=='close_game':
                return '已发送关闭游戏信号,可用 check_game_window 验证'if state.get('client_exit',{}).get('requested')else'错误: '+(state.get('error')or'关闭请求未发送')
            status=state.get('run',{}).get('last_status')or state.get('error')or state['state']
            if self.kind=='open_game':
                return ('成功打开并进入绝区零游戏'if self.enter else'成功打开游戏(未登录)')if success else'打开游戏失败: '+str(status)
            label={'run_one_dragon':'一条龙','run_standalone_app':'独立应用','run_operation':'operation'}[self.kind]
            if self.kind=='run_operation' and success:
                message=(self.record.get('output') or {}).get('message')
                if message:return label+'运行成功。'+message
            return label+'运行成功'if success else label+'运行失败: '+str(status)
        self.worker.join()
        # Capture the original RunSlot result, never a later task's state.
        status=self.execution.backend.query_status()
        success=status.state=='success'
        if self.kind=='open_game':return '成功打开并进入绝区零游戏'if success else'打开游戏失败: '+str(status.last_status)
        labels={'run_one_dragon':'一条龙','run_standalone_app':'独立应用','run_operation':'operation'}
        label=labels[self.kind]
        return label+'运行成功'if success else label+'运行失败: '+str(status.last_status)


def tool_rpc(owner,request,session):
    """Cache one Future per request id, including still-blocking requests.

    HTTP connections may retry while an operation runs. They all await the same
    completion, while stop/pause/status continue to acquire the dispatch lock.
    """
    with owner.lock:
        if owner.closed:raise ValueError('MCP服务正在关闭。')
        if request.get('jsonrpc')!='2.0':raise ValueError('需要JSON-RPC对象')
        params=request.get('params',{});key=request.get('id')
        if type(key)not in(str,int)or not isinstance(params,dict):raise ValueError('工具请求格式无效')
        state=owner.sessions.get(session)
        if state is None or not state['initialized']:raise ValueError('MCP会话未初始化或已失效')
        cache_key=json.dumps(key)
        cached=state['cache'].get(cache_key)
        if cached is not None:
            previous,response=cached
            if previous!=request:raise ValueError('同一请求编号不能对应不同操作')
            if not isinstance(response,Future):return copy.deepcopy(response),{}
            future=response
        else:
            if len(state['cache'])>=128:
                evict=next((name for name,(_,value)in state['cache'].items()if not isinstance(value,Future)or value.done()),None)
                if evict is None:raise ValueError('等待中的请求过多，请先查询或停止原任务。')
                state['cache'].pop(evict)
            future=Future();state['cache'][cache_key]=(copy.deepcopy(request),future)
            def finish(value=None,error=None):
                result=dict(content=[dict(type='text',text=str(error)if error else json.dumps(value,ensure_ascii=False))],isError=error is not None)
                future.set_result(dict(jsonrpc='2.0',id=key,result=result))
            try:
                value=owner.tool(params.get('name'),params.get('arguments',{}))
                if isinstance(value,PendingTool):
                    def wait():
                        try:finish(value.result())
                        except Exception as exc:finish(error=exc)
                    threading.Thread(target=wait,name='AutoZZZ-MCP-Wait',daemon=True).start()
                else:finish(value)
            except Exception as exc:
                owner.log('MCP工具调用被拒绝或失败；未重试操作。');finish(error=exc)
    return copy.deepcopy(future.result()),{}
