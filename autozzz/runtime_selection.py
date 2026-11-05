"""Verified optional SDK identities; the shipped fixed runtime remains an independent fallback."""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re

from .onedragon_bundle import digest,tree_files,relative_name

FORMAT=1
RUNTIME_PARTS=('source','base-python','env','bridge')


def plain(path):
    path=Path(path).absolute()
    if path!=path.resolve():raise ValueError('SDK路径被重定向。')
    current=path
    while current.parent!=current:
        if current.exists()and (current.is_symlink()or getattr(current.lstat(),'st_file_attributes',0)&0x400):
            raise ValueError('SDK路径不能包含链接或重解析点。')
        current=current.parent
    return path


def identifier(value):
    if type(value)is not str or re.fullmatch('[0-9a-f]{32}',value)is None:raise ValueError('SDK候选或资料快照编号无效。')
    return value


def commit(value):
    if type(value)is not str or re.fullmatch('[0-9a-f]{40}',value)is None:raise ValueError('SDK提交版本无效。')
    return value


def read_json(path,limit=32*1024*1024):
    path=plain(path)
    if not path.is_file()or path.stat().st_size>limit:raise ValueError('SDK清单缺失或过大：'+str(path))
    def unique(rows):
        result={}
        for key,value in rows:
            if key in result:raise ValueError('SDK清单含重复字段。')
            result[key]=value
        return result
    return json.loads(path.read_text(encoding='utf-8'),object_pairs_hook=unique)


def store_root(stable_data):
    # Profile junctions may resolve anywhere; SDK code remains in the dedicated per-user tool directory.
    return plain(Path(os.environ.get('LOCALAPPDATA',Path.home()/'.local/share'))/'AutoZZZ/SDKs')
def selection_path(stable_data):return plain(Path(stable_data)/'autozzz/sdk-selection.json')
def snapshot_path(stable_data,data_id):return plain(Path(stable_data)/'autozzz/runtime-data'/identifier(data_id))
def candidate_root(stable_data,candidate_id):return plain(store_root(stable_data)/'candidates'/identifier(candidate_id))


def runtime_files(root):
    root=plain(root);result={}
    for name in RUNTIME_PARTS:
        folder=plain(root/name)
        if not folder.is_dir():raise ValueError('SDK候选缺少目录：'+name)
        for relative,path in tree_files(folder):result[name+'/'+relative]=path
    return result


def tree_hash(values):
    return hashlib.sha256(json.dumps(values,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def bridge_files(root):
    root=plain(root);names=[]
    package=plain(root/'autozzz')
    for path in package.rglob('*.py'):
        if '__pycache__'not in path.parts:names.append(path.relative_to(root).as_posix())
    for name in ('autozzz/onedragon_mcp_catalog.json','LICENSE','LICENSE_NOTICE.md'):
        if (root/name).is_file():names.append(name)
    assets=root/'assets'
    if assets.is_dir():names.extend('assets/'+name for name,_ in tree_files(assets))
    return {name:plain(root/name)for name in sorted(set(names))}


def bridge_fingerprint(root):return tree_hash({'bridge/'+name:digest(path)for name,path in bridge_files(root).items()})


def verify_identity(identity_file,identity_digest,*,source,python,bridge,revision,protocol,require_compatible=True,verify_files=True):
    identity_file=plain(identity_file);root=identity_file.parent
    if identity_file.name!='runtime.json' or digest(identity_file)!=identity_digest:raise ValueError('SDK身份清单摘要已变化。')
    metadata=read_json(identity_file)
    required={'format','candidate_id','revision','protocol','python_version','branch','remote','groups','lock_hash','bridge_hash','files','resources','probe'}
    if type(metadata)is not dict or set(metadata)!=required or metadata['format']!=FORMAT:
        raise ValueError('SDK身份清单格式无效。')
    identifier(metadata['candidate_id']);commit(metadata['revision'])
    if (metadata['revision']!=revision or type(metadata['protocol'])is not int or metadata['protocol']!=protocol
        or type(metadata['python_version'])is not str or not re.fullmatch(r'3\.11\.\d+',metadata['python_version'])):
        raise ValueError('SDK身份版本或协议不符。')
    if (plain(source)!=root/'source'or plain(python)!=root/'env/Scripts/python.exe'or plain(bridge)!=root/'bridge'):
        raise ValueError('SDK身份与实际源码、解释器或桥接路径不符。')
    checksums=metadata['files']
    if type(checksums)is not dict or not checksums or len(checksums)>100000:raise ValueError('SDK文件清单无效。')
    for name,checksum in checksums.items():
        relative_name(name)
        if name.partition('/')[0]not in RUNTIME_PARTS or type(checksum)is not str or not re.fullmatch('[0-9a-f]{64}',checksum):
            raise ValueError('SDK文件摘要或目录无效。')
    required_files={'source/LICENSE','source/uv.lock','source/pyproject.toml','source/src/zzz_od/context/zzz_context.py',
        'base-python/python.exe','base-python/python311.dll','env/Scripts/python.exe','env/pyvenv.cfg',
        'bridge/autozzz/onedragon_worker.py','bridge/autozzz/onedragon_runtime.py','bridge/autozzz/runtime_selection.py'}
    if not required_files.issubset(checksums):raise ValueError('SDK候选缺少完整源码、独立解释器或桥接文件。')
    if verify_files:
        actual=runtime_files(root)
        if actual.keys()!=checksums.keys():raise ValueError('SDK候选文件缺失或多出文件。')
        for name,path in actual.items():
            if digest(path)!=checksums[name]:raise ValueError('SDK候选文件已变化：'+name)
    config={}
    for row in (root/'env/pyvenv.cfg').read_text(encoding='utf-8').splitlines():
        key,separator,value=row.partition('=')
        if separator:config[key.strip()]=value.strip()
    if plain(config.get('home',''))!=root/'base-python':raise ValueError('SDK候选虚拟环境仍依赖外部解释器。')
    if (checksums.get('source/uv.lock')!=metadata['lock_hash']or tree_hash({k:v for k,v in checksums.items()if k.startswith('bridge/')})!=metadata['bridge_hash']):
        raise ValueError('SDK锁文件或桥接摘要不符。')
    if type(metadata['resources'])is not list or any('source/'+relative_name(name)not in checksums or not name.startswith(('config/','assets/'))for name in metadata['resources']):
        raise ValueError('SDK资源清单无效。')
    if type(metadata['probe'])is not dict or metadata['probe'].get('revision')!=revision or require_compatible and metadata['probe'].get('compatible')is not True:
        raise ValueError('SDK候选没有通过该版本的真实兼容探测。')
    return metadata


@dataclass(frozen=True)
class RuntimeSelection:
    source:Path
    python:Path
    data:Path
    bridge:Path
    revision:str
    protocol:int
    identity_file:Path|None
    identity_digest:str|None
    candidate_id:str|None
    kind:str
    stable_data:Path
    selection_digest:str|None
    current_bridge:Path|None=None

    def verify(self):
        path=selection_path(self.stable_data)
        if self.selection_digest is not None and digest(path)!=self.selection_digest:raise ValueError('所选SDK已在另一窗口变化，请重新打开连接。')
        plain(self.data)
        if self.kind=='candidate':
            metadata=verify_identity(self.identity_file,self.identity_digest,source=self.source,python=self.python,
                bridge=self.bridge,revision=self.revision,protocol=self.protocol)
            if self.current_bridge is not None and metadata['bridge_hash']!=bridge_fingerprint(self.current_bridge):
                raise ValueError('当前AutoZZZ桥接代码已更新，请从稳定控制入口重新探测候选SDK或回退；旧候选未自动运行。')
            return metadata
        if self.kind!='stable':raise ValueError('SDK选择类型无效。')
        return dict(revision=self.revision,protocol=self.protocol,stable=True)

    def prepare(self):
        self.verify()
        marker=read_json(self.data/'autozzz-runtime.json',4096)
        if marker.get('revision')!=self.revision or marker.get('protocol')!=self.protocol:raise ValueError('SDK资料快照版本不符，未改写资料。')
        return self.data


def candidate_selection(stable_data,candidate_id,data_id,protocol):
    stable_data=Path(stable_data).resolve();root=candidate_root(stable_data,candidate_id)
    identity=root/'runtime.json';metadata=read_json(identity)
    result=RuntimeSelection(root/'source',root/'env/Scripts/python.exe',snapshot_path(stable_data,data_id),root/'bridge',
        commit(metadata.get('revision')),protocol,identity,digest(identity),candidate_id,'candidate',stable_data,None)
    result.verify();return result


def resolve_selection(stable_data,*,stable_source,stable_python,stable_bridge,stable_bundle,stable_revision,protocol,verify_files=True):
    if (Path(stable_data)/'autozzz/sdk-activation.json').exists():raise ValueError('SDK切换存在待核对事务，请从稳定控制入口恢复切换。')
    # Existing user-selected profiles may be directory junctions. No selection means the old path remains valid.
    if not (Path(stable_data)/'autozzz/sdk-selection.json').exists():return None
    stable_data=Path(stable_data).resolve();path=selection_path(stable_data)
    record=read_json(path,8192)
    if (type(record)is not dict or set(record)!={'format','kind','candidate_id','data_id','revision','protocol','identity_digest'}
        or record['format']!=FORMAT or record['kind']not in ('candidate','stable')or record['protocol']!=protocol):
        raise ValueError('SDK选择记录无效，原固定SDK与全部资料保持不变。')
    data=snapshot_path(stable_data,record['data_id']);revision=commit(record['revision'])
    if record['kind']=='candidate':
        root=candidate_root(stable_data,record['candidate_id']);identity=root/'runtime.json'
        source,python,bridge=root/'source',root/'env/Scripts/python.exe',root/'bridge'
        identity_digest=record['identity_digest']
        if type(identity_digest)is not str or not re.fullmatch('[0-9a-f]{64}',identity_digest):raise ValueError('SDK身份摘要无效。')
    else:
        if record['candidate_id']is not None or record['identity_digest']is not None or revision!=stable_revision:raise ValueError('稳定SDK回退记录无效。')
        source,python,bridge=plain(stable_source),plain(stable_python),plain(stable_bridge)
        identity=identity_digest=None
    result=RuntimeSelection(source,python,data,bridge,revision,protocol,identity,identity_digest,record['candidate_id'],record['kind'],stable_data,digest(path),Path(stable_bridge))
    if verify_files:result.verify()
    elif result.kind=='candidate':
        # GUI discovery validates bounded metadata only. Background verify/prepare still hashes every runtime file and current bridge.
        verify_identity(identity,identity_digest,source=source,python=python,bridge=bridge,revision=revision,protocol=protocol,verify_files=False)
    return result
