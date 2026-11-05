"""Fixed-vendor MCP registrations, without starting the vendor's unguarded server.

The capture adapter changes only transport imports. Tool bodies, annotations and
prompt renderers are compiled from the pinned repository, never copied by hand.
Catalog snapshots allow discovery without initializing SDK models/controllers.
"""
from __future__ import annotations

import ast
import asyncio
import copy
import hashlib
import inspect
import json
from pathlib import Path
import types
import typing
from types import SimpleNamespace


FILES = ('prompts.py', 'config_app.py', 'service_app.py', 'app.py')
READ_TOOLS = frozenset(('check_game_window', 'capture_game_screen', 'analyze_screen',
    'list_applications', 'get_predefined_teams', 'list_operations', 'describe_operation',
    'get_config', 'describe_config', 'list_app_configs',
    'list_mcp_usage_guides', 'get_mcp_usage_guide'))
RUN_TOOLS = frozenset(('run_one_dragon', 'run_standalone_app', 'run_operation', 'open_game'))


class Registrations:
    def __init__(self, name, instructions=''):
        self.name, self.instructions = name, instructions
        self.tools, self.prompts = {}, {}

    def tool(self, **metadata):
        def register(function):
            self.tools[metadata.get('name', function.__name__)] = (function, metadata)
            return function
        return register

    def prompt(self, **metadata):
        def register(function):
            self.prompts[metadata.get('name', function.__name__)] = (function, metadata)
            return function
        return register


def annotations(**values):
    aliases = dict(read_only_hint='readOnlyHint', destructive_hint='destructiveHint',
                   idempotent_hint='idempotentHint', open_world_hint='openWorldHint')
    return {aliases.get(key, key): value for key, value in values.items()}


class FieldDescription:
    def __init__(self, **values):
        self.values = values


def vendor_directory(source):
    return Path(source) / 'src/zzz_od/backend/mcp'


def source_hashes(source):
    directory = vendor_directory(source)
    return {name: hashlib.sha256((directory/name).read_bytes().replace(b'\r\n',b'\n')).hexdigest() for name in FILES}


def load_registrations(source, backend):
    """Execute trusted fixed source with a registration-only MCP replacement.

    No sys.modules stubs, installed dependency changes, server or RunSlot is
    created. Other upstream imports retain their original behavior.
    """
    directory = vendor_directory(source)
    namespaces = {}
    for filename in FILES:
        path = directory/filename
        tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        namespace = dict(__name__='autozzz_vendor_mcp_'+path.stem,
                         MCPServer=Registrations, ToolAnnotations=annotations, Field=FieldDescription)
        body = []
        for node in tree.body:
            if isinstance(node, ast.ImportFrom):
                module = node.module or ''
                if module == 'mcp' or module.startswith('mcp.'):
                    continue
                if module == 'pydantic' and all(alias.name=='Field' for alias in node.names):
                    continue
                if module.startswith('zzz_od.backend.mcp.'):
                    dependency = namespaces[module.rsplit('.', 1)[1]]
                    for alias in node.names:
                        namespace[alias.asname or alias.name] = dependency[alias.name]
                    continue
            body.append(node)
        tree.body = body
        exec(compile(tree, str(path), 'exec', dont_inherit=True), namespace)
        if path.stem=='service_app':
            namespace['operation_registry'] = source_operation_registry(namespace['operation_registry'],source)
        namespaces[path.stem] = namespace
    return namespaces['app']['create_mcp_server'](backend)


def source_operation_registry(registry, source):
    """Keep original scanning logic, resolve code against source not data.

    Upstream assumes <work_dir>/src. AutoZZZ deliberately separates writable
    profile data from pinned code. Cloned globals keep this adaptation local,
    including its scan cache; no process-global monkey patch or cwd change.
    """
    src_root=(Path(source)/'src').resolve()
    def modules(package):
        if package not in registry._SCAN_ROOTS:raise ValueError('非法operation扫描包。')
        package_dir=src_root.joinpath(*package.split('.'))
        if not package_dir.is_dir():return
        for path in package_dir.rglob('*.py'):
            if path.name!='__init__.py':
                yield package+'.'+'.'.join(path.relative_to(package_dir).with_suffix('').parts)
    original=registry.scan_operations
    local_globals=dict(original.__globals__,_iter_py_modules=modules,_CACHE=None)
    adapted=types.FunctionType(original.__code__,local_globals,original.__name__,original.__defaults__,original.__closure__)
    return SimpleNamespace(**dict(vars(registry),scan_operations=adapted))


def registration_schema(function):
    """JSON schema for the vendor's JSON-only annotated parameter subset.

    The fixed runtime does not ship pydantic/mcp. Only annotation metadata is
    replaced; this is not a general Pydantic replacement or coercion layer.
    """
    properties, required = {}, []
    for name, parameter in inspect.signature(function).parameters.items():
        value = type_schema(parameter.annotation)
        value['title'] = name.replace('_', ' ').title()
        if parameter.default is inspect.Parameter.empty:
            required.append(name)
        else:
            value['default'] = parameter.default
        properties[name] = value
    result = dict(type='object', properties=properties)
    if required:result['required'] = required
    return result


def type_schema(annotation):
    origin, args = typing.get_origin(annotation), typing.get_args(annotation)
    if origin is typing.Annotated:
        value = type_schema(args[0])
        for metadata in args[1:]:
            if isinstance(metadata, FieldDescription):value.update(metadata.values)
        return value
    if origin in (typing.Union, types.UnionType):return dict(anyOf=[type_schema(arg) for arg in args])
    if origin is typing.Literal:return dict(enum=list(args), type='string')
    if origin is list:return dict(type='array',items=type_schema(args[0]) if args else {})
    if origin is dict or annotation is dict:return dict(type='object')
    kind = {str:'string',int:'integer',float:'number',bool:'boolean',type(None):'null'}.get(annotation)
    if kind:return dict(type=kind)
    raise ValueError('上游MCP出现尚未支持的参数类型：'+str(annotation))


def validate_value(value, schema):
    if 'anyOf' in schema:
        return any(validate_value(value, item) for item in schema['anyOf'])
    kind = schema.get('type')
    matches = {'string':lambda:type(value)is str,'integer':lambda:type(value)is int,
        'number':lambda:type(value)in(int,float),'boolean':lambda:type(value)is bool,
        'null':lambda:value is None,'array':lambda:type(value)is list,
        'object':lambda:type(value)is dict}
    if kind in matches and not matches[kind]():return False
    if 'enum' in schema and value not in schema['enum']:return False
    if kind=='array':return all(validate_value(item,schema.get('items',{})) for item in value)
    return True


def generate_catalog(source):
    registry = load_registrations(source, None)
    tools = [dict(name=name, description=inspect.getdoc(function) or '',
                  inputSchema=registration_schema(function), **metadata)
             for name, (function, metadata) in registry.tools.items()]
    prompts = []
    for name, (function, metadata) in registry.prompts.items():
        args = [dict(name=key, required=value.default is inspect.Parameter.empty)
                for key, value in inspect.signature(function).parameters.items()]
        prompts.append(dict(name=name, arguments=args, **{key: value for key, value in metadata.items() if key!='name'}))
    return dict(source_hashes=source_hashes(source), instructions=registry.instructions, tools=tools, prompts=prompts)


def load_prompt_registrations(source):
    path = vendor_directory(source)/'prompts.py'
    tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    tree.body = [node for node in tree.body if not isinstance(node,ast.ImportFrom)
                 or not (node.module or '').startswith('mcp.')]
    namespace = dict(MCPServer=Registrations, ToolAnnotations=annotations)
    exec(compile(tree,str(path),'exec',dont_inherit=True),namespace)
    registry = Registrations('guides')
    namespace['register_prompts'](registry)
    namespace['register_prompt_tools'](registry)
    return registry


class ReadBackend:
    """Only allowlisted read methods; execution always belongs to the service."""
    def __init__(self, service):
        self.service = service

    @property
    def ctx(self):
        return self.service.ctx

    def __getattr__(self, name):
        if name not in ('check_window', 'capture', 'analyze', 'list_applications', 'list_predefined_teams'):
            raise RuntimeError('上游写入和运行方法不可绕过AutoZZZ授权。')
        from zzz_od.backend.backend_context import ZzzBackendContext
        # Bind the original read methods to a view of the existing context. No
        # constructor: it would allocate a second, uncontrolled RunSlot.
        backend = object.__new__(ZzzBackendContext)
        backend._ctx = self.ctx
        return getattr(backend, name)


class UpstreamCompatibility:
    def __init__(self, service):
        self.service = service
        self.source = Path(getattr(service, 'source', Path(__file__).resolve().parents[1]/'vendor/onedragon'))
        self.catalog = json.loads(Path(__file__).with_name('onedragon_mcp_catalog.json').read_text(encoding='utf-8'))
        if self.catalog['source_hashes'] != source_hashes(self.source):
            raise RuntimeError('固定上游MCP源码已变化，请更新兼容目录并重新验证；未加载旧签名。')
        self.registry = None
        self.guide_registry = None

    def tools(self):
        self.verify_source()
        return copy.deepcopy(self.catalog['tools'])

    def prompts(self):
        self.verify_source()
        return copy.deepcopy(self.catalog['prompts'])

    def verify_source(self):
        if self.catalog['source_hashes'] != source_hashes(self.source):
            raise RuntimeError('固定上游MCP源码已变化，兼容授权与签名已失效。')

    def load(self):
        self.verify_source()
        if self.registry is None:
            self.registry = load_registrations(self.source, ReadBackend(self.service))
        return self.registry

    def validate(self, name, arguments):
        """Validate the original signature; JSON types are not coerced."""
        entry = next((item for item in self.catalog['tools'] if item['name']==name),None)
        if entry is None:raise ValueError('未知上游工具。')
        schema = entry['inputSchema'];properties = schema['properties']
        if not isinstance(arguments,dict) or set(arguments)-set(properties):raise ValueError('工具参数名称无效。')
        if set(schema.get('required',[]))-set(arguments):raise ValueError('工具缺少必填参数。')
        values = {key:copy.deepcopy(arguments.get(key,properties[key].get('default'))) for key in properties}
        for key, value in values.items():
            if not validate_value(value, properties[key]):raise ValueError('参数类型不符合上游签名：'+key)
        return values

    def guides(self):
        self.verify_source()
        if self.guide_registry is None:self.guide_registry = load_prompt_registrations(self.source)
        return self.guide_registry

    def call_read(self, name, arguments):
        if name not in READ_TOOLS:
            raise ValueError('该上游工具需本地授权，未通过只读通道执行。')
        values = self.validate(name, arguments)
        if name=='describe_operation':
            # Importing a Python module executes its top-level code. Constrain
            # this nominally read-only tool to the pinned scanner's inventory.
            registry=self.load().tools['list_operations'][0]()
            allowed={item.op_id for item in registry.operations}
            if values['op_id'] not in allowed:
                raise ValueError('op_id不在固定上游operation扫描清单中；未导入任意模块。')
        registry = self.guides() if name in ('list_mcp_usage_guides','get_mcp_usage_guide') else self.load()
        function, _ = registry.tools[name]
        result = function(**values)
        if inspect.isawaitable(result):
            result = asyncio.run(result)
        return to_plain(result)

    def prompt(self, name, arguments):
        entry = self.guides().prompts.get(name)
        if entry is None:
            raise ValueError('未知的上游提示词。')
        function, metadata = entry
        inspect.signature(function).bind(**arguments)
        if any(not isinstance(value, str) for value in arguments.values()):
            raise ValueError('提示词参数须为字符串。')
        return dict(description=metadata.get('description', ''), messages=[dict(role='user',
            content=dict(type='text', text=function(**arguments)))])


def to_plain(value):
    # Importing OneDragonService here would drag SDK imports into guide-only use.
    import dataclasses
    from enum import Enum
    if dataclasses.is_dataclass(value):return to_plain(dataclasses.asdict(value))
    if isinstance(value,Enum):return to_plain(value.value)
    if isinstance(value,(list,tuple)):return [to_plain(item) for item in value]
    if isinstance(value,dict):return {str(key):to_plain(item) for key,item in value.items()}
    if value is None or type(value)in(str,int,float,bool):return value
    raise ValueError('上游工具返回了无法序列化的类型：'+type(value).__name__)
