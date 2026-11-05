"""Loopback MCP transport over the existing, budgeted AutoZZZ execution service.

MCP 2025-03-26 Streamable HTTP JSON responses; no arbitrary upstream input tools.
https://modelcontextprotocol.io/specification/2025-03-26/basic/transports
"""
from __future__ import annotations

from collections import deque
import copy
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
import threading
import time

from .battle_budget import validate_runtime_budget
from .onedragon_control import validate_runtime_timeout
from .onedragon_mcp_compat import UpstreamCompatibility, READ_TOOLS, RUN_TOOLS, to_plain
from .onedragon_mcp_edit import McpEditing, EDIT_TOOLS


TOOLS = (
    ('get_run_status', '读取 AutoZZZ 当前运行状态', {}),
    ('run_prepared_application', '执行界面已授权的应用一次；不接受新应用、预算或资源权限', {}),
    ('pause_run', '暂停本 MCP 会话启动的任务', {'paused': {'type': 'boolean'}}),
    ('stop_run', '停止本 MCP 会话启动的任务；等待其原工作线程退出', {}),
)


class McpService:
    def __init__(self, service):
        self.service = service
        # The stdio worker must use this same lock around all service dispatch.
        self.lock = getattr(service,'_dispatch_lock',None) or threading.RLock()
        self.server = self.thread = None
        self.token = ''
        self.sessions = {}
        self.logs = deque(maxlen=200)
        self.grant = None
        self.owned_job = ''
        self.owned_run = {}
        self.closed = False
        self._compat = None
        self.editing = McpEditing(self)

    def edit_enable(self,scopes):
        with self.lock:
            if self.closed or self.thread is None or not self.thread.is_alive():raise ValueError('请先启动MCP服务。')
            self.editing.enable(scopes)
            self.log('已在本地启用本次MCP编辑会话。')
            return self.status()

    def edit_disable(self):
        with self.lock:
            self.editing.disable();self.log('MCP编辑会话已关闭。')
            return self.status()

    def edit_restore(self,kind,id):
        with self.lock:
            self.editing.restore(kind,id)
            return self.status()

    @property
    def compatibility(self):
        if self._compat is None:self._compat = UpstreamCompatibility(self.service)
        return self._compat

    def tool_catalog(self):
        upstream = self.compatibility.tools()
        for entry in upstream:
            name=entry['name']
            state='read_only' if name in READ_TOOLS or name=='get_run_status' else (
                'owned_run_only' if name=='stop_run' else (
                'local_single_use_grant_nonblocking' if name in ('run_one_dragon','run_standalone_app') else (
                'local_revision_bound_edit_session' if name in EDIT_TOOLS else 'unavailable_pending_local_authorization_adapter')))
            entry['_meta']={'autozzz/availability':state}
        original_names = {item['name'] for item in upstream}
        extra = [dict(name=name,description=description,inputSchema=dict(type='object',
            properties=properties,required=list(properties),additionalProperties=False))
            for name,description,properties in TOOLS if name not in original_names]
        return upstream+extra

    def log(self, text):
        self.logs.append(dict(time=time.time(), message=text))

    def status(self):
        with self.lock:
            running = self.thread is not None and self.thread.is_alive()
            execution = self.service.execution_status()
            grant = self.grant
            return dict(running=running, url=f'http://127.0.0.1:{self.server.server_port}/mcp' if running else '',
                protocol='2025-03-26', transport='streamable-http-json', owned_job=self.owned_job,
                granted=None if grant is None else dict(app_id=grant['app_id'], budget=copy.deepcopy(grant['budget']),
                    expires_at=grant['expires_at'], consumed=grant['consumed']),
                execution=execution, editing=self.editing.status(), logs=list(self.logs))

    def start(self, port=23001):
        if type(port) is not int or not 1024 <= port <= 65535:
            raise ValueError('MCP端口须为1024—65535。')
        with self.lock:
            if self.closed:raise RuntimeError('MCP管理器已关闭。')
            if self.thread is not None and self.thread.is_alive():raise RuntimeError('MCP服务已启动，请使用原连接。')
            self.token = secrets.token_urlsafe(32)
            self.sessions.clear()
            self.server = ThreadingHTTPServer(('127.0.0.1', port), self._handler())
            self.server.daemon_threads = True
            self.thread = threading.Thread(target=self.server.serve_forever, name='AutoZZZ-MCP', daemon=True)
            self.thread.start()
            self.log('MCP服务已启动，仅接受回环连接和本次令牌。')
            return dict(self.status(), bearer_token=self.token)

    def authorize(self, budget=None, timeout_seconds=600, resource_confirmation=None, expires_seconds=900):
        """Called by local UI RPC only, never exposed as an MCP tool."""
        validate_runtime_timeout(timeout_seconds)
        if type(expires_seconds) is not int or not 30 <= expires_seconds <= 3600:
            raise ValueError('MCP授权有效期须为30—3600秒。')
        with self.lock:
            if self.closed or not self.thread or not self.thread.is_alive():raise RuntimeError('请先启动MCP服务。')
            self.service._assert_idle()
            execution = self.service._execution
            if execution is None or execution.state != 'prepared' or not execution.prepared_app:
                raise ValueError('请在运行窗口准备应用后再授权。')
            if execution.prepared_hash != execution.configuration_digest():raise ValueError('配置已变化，请重新准备。')
            if execution.uses_charge:
                validate_runtime_budget(budget)
                # Open existing ledger only. A grant must never create/renew an allowance.
                from .battle_budget import budget_directory
                directory = budget_directory()
                ledger_path=directory/(budget['id']+'.json')
                if not ledger_path.is_file():
                    raise ValueError('MCP只能沿用已有预算，请先在正常运行流程建立预算。')
                server = 'cn' if execution.ctx.game_account_config.game_region in ('cn','cn_b') else 'global'
                ledger=json.loads(ledger_path.read_text(encoding='utf-8'))
                expected=dict(id=budget['id'],server=server,allowance=budget['allowance'],battle_limit=budget['battle_limit'])
                if any(ledger.get(key)!=value for key,value in expected.items()):raise ValueError('现有预算与授权上限不一致。')
                if not isinstance(ledger.get('entries'),list):raise ValueError('预算记录无效。')
                if any(row.get('status')=='pending' for row in ledger['entries']):raise ValueError('预算存在待核对出战，请先续接原任务。')
            elif budget is not None:raise ValueError('当前应用不需要预算。')
            execution.resource_confirmation.verify(resource_confirmation,execution.prepared_hash)
            self.grant=dict(app_id=execution.prepared_app, prepared_hash=execution.prepared_hash,
                prepared_job=execution.job_id, budget=copy.deepcopy(budget), timeout_seconds=timeout_seconds,
                resource_confirmation=resource_confirmation, expires_at=time.time()+expires_seconds, consumed=False)
            self.log('已单次授权当前准备的应用：'+execution.prepared_app)
            return self.status()

    def revoke(self):
        with self.lock:
            self.grant = None
            self.log('已撤销未使用的MCP执行授权；正在运行的原任务保持原状态。')
            return self.status()

    def tool(self, name, arguments):
        with self.lock:
            if self.closed:raise RuntimeError('MCP服务已关闭。')
            if not isinstance(arguments,dict):raise ValueError('工具参数须为对象。')
            if name in READ_TOOLS:return self.compatibility.call_read(name,arguments)
            if name in EDIT_TOOLS:return self.editing.call(name,arguments)
            upstream_names = {item['name'] for item in self.compatibility.tools()}
            if name in upstream_names and name not in ('get_run_status','stop_run'):
                values=self.compatibility.validate(name,arguments)
                if name in RUN_TOOLS:
                    if values.get('block'):
                        raise ValueError('受控MCP暂不支持阻塞等待，请设block=false并用get_run_status查询原任务。')
                    execution=self.service._execution
                    app=getattr(execution,'prepared_app','')
                    if name=='run_one_dragon':target='one_dragon'
                    elif name=='run_standalone_app':target=values.get('app_id') or app
                    else:
                        raise ValueError('该入口尚缺绑定本地准备任务的专用授权；请使用AutoZZZ原登录或operation调试入口。未执行输入。')
                    if not target or target!=app:
                        raise ValueError('指定应用与本地已准备应用不一致；请在原运行窗口重新准备并单次授权。')
                    result=self.tool('run_prepared_application',{})
                    return dict(started=True,source='mcp',app=app,job_id=result['job_id'],hint='用get_run_status查原任务进度与结果')
                raise ValueError('该上游写入/输入工具尚未绑定本地单次授权，当前不执行；请使用AutoZZZ本地编辑器或受控operation调试入口。')
            if name not in {tool[0] for tool in TOOLS}:raise ValueError('未开放该工具。')
            expected={'paused'} if name=='pause_run' else set()
            if set(arguments)!=expected:raise ValueError('工具参数无效；应用和预算只能在本地界面授权。')
            if name=='get_run_status':
                result=copy.deepcopy(self.service.execution_status())
                # Preparation tokens are local consent, not remote execution capabilities.
                result.get('resources',{}).pop('confirmation_id',None)
                backend=getattr(self.service._execution,'backend',None)
                if backend is not None:
                    return dict(to_plain(backend.query_status()),autozzz=result)
                return dict(state='idle',source=None,app=None,started_at=None,duration_seconds=None,
                    current_node=None,retry_count=None,last_status=None,failed_node=None,autozzz=result)
            if name=='run_prepared_application':
                if getattr(self.service,'_rotation_guard',None) is not None or getattr(self.service,'_rotation_sealed',False):
                    raise ValueError('账号轮换期间不可使用独立MCP执行。')
                grant=self.grant
                if grant is None or grant['consumed'] or time.time()>grant['expires_at']:
                    raise ValueError('没有有效的单次执行授权，请在AutoZZZ界面重新授权。')
                execution=self.service._execution
                if (execution is None or execution.busy or execution.state!='prepared'
                    or execution.job_id!=grant['prepared_job'] or execution.prepared_app!=grant['app_id']
                    or execution.prepared_hash!=grant['prepared_hash']
                    or execution.configuration_digest()!=grant['prepared_hash']):
                    raise ValueError('准备任务或配置已变化，旧授权失效。')
                if grant['budget'] is not None:
                    from .battle_budget import budget_directory
                    if not (budget_directory()/(grant['budget']['id']+'.json')).is_file():
                        raise ValueError('原预算记录已不存在，请先人工核对。')
                grant['consumed']=True  # A failed/ambiguous start cannot be replayed.
                self.editing.session=None
                result=self.service.start_application(grant['app_id'],budget=copy.deepcopy(grant['budget']),
                    timeout_seconds=grant['timeout_seconds'],resource_confirmation=grant['resource_confirmation'])
                self.owned_job=result['job_id']
                self.owned_run=dict(job_id=self.owned_job,prepared_job=grant['prepared_job'],app_id=grant['app_id'],
                    budget=copy.deepcopy(grant['budget']),timeout_seconds=grant['timeout_seconds'],source='mcp')
                self.log('MCP已提交一次执行，后续仅查询原任务。')
                result=copy.deepcopy(result)
                result.get('resources',{}).pop('confirmation_id',None)
                return result
            current=self.service.execution_status()
            if not self.owned_job or current.get('job_id')!=self.owned_job:
                raise ValueError('当前任务不是本MCP会话启动的任务。')
            if name=='pause_run':
                if type(arguments['paused'])is not bool:raise ValueError('paused须为布尔值。')
                result=self.service.pause_execution(arguments['paused'])
                self.log('MCP已请求暂停原任务。' if arguments['paused'] else 'MCP已请求继续原任务。')
                result=copy.deepcopy(result);result.get('resources',{}).pop('confirmation_id',None)
                return result
            result=self.service.stop_execution();self.log('MCP已请求停止原任务，运行锁保留到退出。')
            result=copy.deepcopy(result);result.get('resources',{}).pop('confirmation_id',None)
            return result

    def _handler(self):
        owner=self
        class Handler(BaseHTTPRequestHandler):
            protocol_version='HTTP/1.1'
            def log_message(self,*_):pass
            def setup(self):
                super().setup();self.connection.settimeout(3)
            def respond(self,status,body=None,headers=None):
                encoded=b'' if body is None else json.dumps(body,ensure_ascii=False,allow_nan=False).encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(encoded)))
                self.send_header('Connection','close')
                for key,value in (headers or {}).items():self.send_header(key,value)
                self.end_headers()
                if encoded:self.wfile.write(encoded)
                self.close_connection=True
            def authorized(self):
                if self.path!='/mcp':self.respond(404);return False
                if self.headers.get('Host') not in (f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'):
                    self.respond(403);return False
                origin=self.headers.get('Origin')
                if origin is not None and origin not in (f'http://127.0.0.1:{self.server.server_port}',f'http://localhost:{self.server.server_port}'):
                    self.respond(403);return False
                if not owner.token or not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+owner.token):
                    self.respond(401);return False
                return True
            def do_GET(self):
                if self.authorized():self.respond(405)
            def do_DELETE(self):
                if not self.authorized():return
                session=self.headers.get('Mcp-Session-Id')
                with owner.lock:owner.sessions.pop(session,None)
                self.respond(200)
            def do_POST(self):
                if not self.authorized():return
                try:
                    protocol=self.headers.get('MCP-Protocol-Version')
                    if protocol is not None and protocol!='2025-03-26':self.respond(400);return
                    size=int(self.headers.get('Content-Length','0'))
                    if not 0<size<=65536 or self.headers.get('Transfer-Encoding'):raise ValueError('请求大小无效')
                    if self.headers.get_content_type()!='application/json':self.respond(415);return
                    accept=self.headers.get('Accept','')
                    if 'application/json' not in accept or 'text/event-stream' not in accept:self.respond(406);return
                    request=json.loads(self.rfile.read(size),parse_constant=lambda value:(_ for _ in ()).throw(ValueError('非法JSON数值：'+value)))
                    session=self.headers.get('Mcp-Session-Id')
                    if isinstance(request,list):
                        if not request or len(request)>16 or any(not isinstance(row,dict) or row.get('method')=='initialize' for row in request):
                            raise ValueError('批次请求无效')
                        pairs=[owner.rpc(row,session) for row in request]
                        replies=[value for value,_ in pairs if value is not None]
                        response=replies or None;headers={}
                    else:response,headers=owner.rpc(request,session)
                    self.respond(202 if response is None else 200,response,headers)
                except (ValueError,TypeError,UnicodeError,json.JSONDecodeError) as exc:
                    self.respond(400,dict(jsonrpc='2.0',id=None,error=dict(code=-32600,message=str(exc))))
                except Exception:
                    self.respond(500,dict(jsonrpc='2.0',id=None,error=dict(code=-32603,message='服务处理失败')))
        return Handler

    def run_description(self):
        return copy.deepcopy(self.owned_run)

    def rpc(self,request,session):
        with self.lock:
            if self.closed:raise ValueError('MCP服务正在关闭。')
            if not isinstance(request,dict) or request.get('jsonrpc')!='2.0':raise ValueError('需要单个JSON-RPC对象')
            method=request.get('method');params=request.get('params',{});key=request.get('id')
            if not isinstance(method,str) or not isinstance(params,dict):raise ValueError('请求格式无效')
            headers={}
            if method=='initialize':
                if type(key) not in (str,int) or not isinstance(params.get('protocolVersion'),str):raise ValueError('初始化请求无效')
                if len(self.sessions)>=16:raise ValueError('会话数已达到上限，请关闭旧连接')
                session=secrets.token_hex(24)
                self.sessions[session]=dict(initialized=False,cache={})
                headers['Mcp-Session-Id']=session
                result=dict(protocolVersion='2025-03-26',capabilities=dict(tools=dict(listChanged=False),prompts=dict(listChanged=False)),
                    serverInfo=dict(name='AutoZZZ',version='1'),
                    instructions='AutoZZZ兼容层：只读工具及指南保留。运行需本地单次授权，仅支持非阻塞且沿用原任务和预算。目录_meta说明可用性；写入/输入/登录等未适配入口会明确拒绝。以下为固定上游原说明，不能据此绕过上述授权边界。\n\n'+self.compatibility.catalog['instructions'])
            else:
                state=self.sessions.get(session)
                if state is None:raise ValueError('MCP会话已失效，请重新初始化')
                if method=='notifications/initialized':state['initialized']=True;return None,headers
                if 'id' not in request:return None,headers
                if type(key)not in (str,int):raise ValueError('请求编号无效')
                cache_key=json.dumps(key)
                if cache_key in state['cache']:
                    old_request,response=state['cache'][cache_key]
                    if old_request!=request:raise ValueError('同一请求编号不能对应不同操作')
                    return copy.deepcopy(response),headers
                if not state['initialized']:raise ValueError('请先完成MCP初始化')
                if method=='ping':result={}
                elif method=='tools/list':
                    result=dict(tools=self.tool_catalog())
                elif method=='prompts/list':result=dict(prompts=self.compatibility.prompts())
                elif method=='prompts/get':
                    try:result=self.compatibility.prompt(params.get('name'),params.get('arguments',{}))
                    except (ValueError,TypeError) as exc:
                        return dict(jsonrpc='2.0',id=key,error=dict(code=-32602,message=str(exc))),headers
                elif method=='tools/call':
                    try:
                        value=self.tool(params.get('name'),params.get('arguments',{}))
                        result=dict(content=[dict(type='text',text=json.dumps(value,ensure_ascii=False))],isError=False)
                    except Exception as exc:
                        self.log('MCP工具调用被拒绝或失败；未重试操作。')
                        result=dict(content=[dict(type='text',text=str(exc))],isError=True)
                else:return dict(jsonrpc='2.0',id=key,error=dict(code=-32601,message='未开放的方法')),headers
            response=dict(jsonrpc='2.0',id=key,result=result)
            if method!='initialize':
                if len(state['cache'])>=128:state['cache'].pop(next(iter(state['cache'])))
                state['cache'][cache_key]=(copy.deepcopy(request),copy.deepcopy(response))
            return response,headers

    def stop(self):
        with self.lock:
            self.grant=None
            self.editing.session=None
            if self.owned_job and self.service.execution_status().get('job_id')==self.owned_job:
                self.service.stop_execution()
            server,thread=self.server,self.thread
            self.server=self.thread=None;self.sessions.clear();self.token=''
            self.log('MCP服务已停止；如原任务仍退出中，其运行锁继续保留。')
        if server is not None:server.shutdown();server.server_close()
        if thread is not None:thread.join(timeout=5)
        return self.status()

    def close(self):
        with self.lock:self.closed=True
        self.stop()
