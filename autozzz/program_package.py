"""完整程序包的文件清单；账号、报告和用户方案不属于程序文件。"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path,PurePosixPath
import re
import shutil
import stat
import zipfile

from .onedragon_bundle import digest,filesystem_path,read_manifest as read_runtime_manifest,MANIFEST_NAME

MANIFEST='autozzz-package.json'
USER_FILES={'config.json','battle.json','training_catalog.json'}
MAX_PACKAGE_BYTES=8*1024**3


def relative(value):
    if type(value)is not str or not value or '\\'in value or ':'in value:raise ValueError('程序包路径无效。')
    path=PurePosixPath(value)
    if path.is_absolute() or str(path)!=value or any(part in ('','.','..') or part.endswith(('.',' '))
        or any(ord(c)<32 or c in '<>"|?*' for c in part) or part.split('.')[0].upper()in {'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))} for part in value.split('/')):
        raise ValueError('程序包路径无效。')
    return value


def disposable(name):return '__pycache__'in PurePosixPath(name).parts or name.endswith('.pyc')


def user_file(name):return name.split('/')[0].casefold()in USER_FILES|{'runs'}


def files(root):
    root=filesystem_path(Path(root).resolve())
    for folder,dirs,names in os.walk(root,followlinks=False):
        for name in dirs+names:
            path=Path(folder)/name
            if path.is_symlink() or getattr(path.lstat(),'st_file_attributes',0)&0x400:raise ValueError('程序目录包含链接，不能自动替换。')
        for name in names:
            path=Path(folder)/name
            if not stat.S_ISREG(path.stat().st_mode):raise ValueError('程序目录包含非普通文件。')
            key=relative(path.relative_to(root).as_posix())
            if not disposable(key):yield key,path
        dirs[:]=[name for name in dirs if name!='__pycache__']


def _pairs(values):
    result={}
    for key,value in values:
        if key in result:raise ValueError('程序包清单有重复字段。')
        result[key]=value
    return result


def read_manifest(root):
    path=filesystem_path(Path(root)/MANIFEST)
    if not path.is_file():raise ValueError('程序包缺少 AutoZZZ 文件清单。')
    if path.is_symlink() or getattr(path.lstat(),'st_file_attributes',0)&0x400 or path.stat().st_size>64*1024**2:raise ValueError('程序包清单无效。')
    data=json.loads(path.read_text(encoding='utf-8'),object_pairs_hook=_pairs)
    if (type(data)is not dict or set(data)!={'format','product','version','base_commit','runtime_revision','protocol','files'}
        or type(data['format'])is not int or data['format']!=1 or data['product']!='AutoZZZ'
        or type(data['version'])is not str or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,79}',data['version'])is None
        or type(data['base_commit'])is not str or re.fullmatch('[0-9a-f]{40}',data['base_commit'])is None
        or type(data['runtime_revision'])is not str or re.fullmatch('[0-9a-f]{40}',data['runtime_revision'])is None
        or type(data['protocol'])is not int or data['protocol']<1 or type(data['files'])is not dict or not data['files'] or len(data['files'])>200000):
        raise ValueError('不是受支持的 AutoZZZ 完整程序包。')
    seen=set()
    for name,checksum in data['files'].items():
        relative(name)
        if name.casefold()in seen or name==MANIFEST or user_file(name) or disposable(name) or type(checksum)is not str or re.fullmatch('[0-9a-f]{64}',checksum)is None:
            raise ValueError('程序包文件清单无效。')
        seen.add(name.casefold())
    if not {'AutoZZZ.exe','onedragon/autozzz-bundle.json'}.issubset(data['files']):raise ValueError('程序包缺少主程序或独立运行时。')
    return data


def verify_package(root,*,strict=False):
    root=Path(root).resolve();data=read_manifest(root)
    actual=dict(files(root));actual.pop(MANIFEST,None)
    if strict and set(actual)!=set(data['files']):raise ValueError('更新包夹带额外文件或用户资料。')
    for name,expected in data['files'].items():
        if name not in actual or digest(actual[name])!=expected:raise ValueError('程序文件摘要不符：'+name)
    verify_runtime_index(root,data,actual)
    return data


def verify_runtime_index(root,data,actual):
    """外层已核实同一批文件摘要；再核对 SDK 清单，无需重复读取全部 DLL。"""
    runtime=read_runtime_manifest(Path(root)/'onedragon',data['runtime_revision'],data['protocol'])
    nested={name.removeprefix('onedragon/') for name in actual if name.startswith('onedragon/') and name!='onedragon/'+MANIFEST_NAME}
    if nested!=set(runtime['files']):raise ValueError('独立运行时文件清单不一致。')
    if any(data['files'].get('onedragon/'+name)!=checksum for name,checksum in runtime['files'].items()):raise ValueError('独立运行时摘要与完整程序包不一致。')
    return runtime


def create_manifest(root,version,base_commit,*,reindex=False):
    root=Path(root).resolve()
    bundle=json.loads((root/'onedragon/autozzz-bundle.json').read_text(encoding='utf-8'))
    managed=set(read_manifest(root)['files']) if (root/MANIFEST).exists() and not reindex else None
    entries={name:digest(path) for name,path in files(root) if name!=MANIFEST and not user_file(name) and (managed is None or name in managed)}
    if managed is not None and set(entries)!=managed:raise ValueError('原清单中的程序文件缺失，不能重新标记发布包。')
    data=dict(format=1,product='AutoZZZ',version=version,base_commit=base_commit,runtime_revision=bundle['revision'],protocol=bundle['protocol'],files=entries)
    verify_runtime_index(root,data,entries)
    path=root/MANIFEST
    original=path.read_bytes() if path.exists() else None
    try:
        path.write_text(json.dumps(data,ensure_ascii=False,sort_keys=True,indent=2),encoding='utf-8');read_manifest(root)
    except BaseException:
        if original is None:path.unlink(missing_ok=True)
        else:path.write_bytes(original)
        raise
    return data


def preserve_user_files(source,target):
    """构建发布也沿用更新器的数据边界；有清单时只复制非程序文件。"""
    source,target=Path(source).resolve(),Path(target).resolve()
    if source==target or target.is_relative_to(source):raise ValueError('资料复制目标不能在原程序目录内。')
    new=read_manifest(target);list(files(target))
    old=read_manifest(source) if (source/MANIFEST).exists() else None
    legacy={'AutoZZZ.exe','LICENSE','LICENSE_NOTICE.md','README.md','DAILY_FLOW.md','BATTLE.md','VALIDATION.md','ONEDRAGON_INTEGRATION.md'}
    def managed(name):return name in old['files'] if old is not None else name in legacy or name.split('/')[0]in {'_internal','onedragon','docs'}
    users={name:path for name,path in files(source) if name!=MANIFEST and not managed(name)}
    hashes={name:digest(path) for name,path in users.items()};new_names={name.casefold() for name in new['files']}
    for name,path in users.items():
        if name.casefold()in new_names:raise ValueError('新程序与用户资料同名：'+name)
        destination=filesystem_path(target/name);destination.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,destination)
        if digest(destination)!=hashes[name]:raise ValueError('资料复制未核实：'+name)
    current={name:digest(path) for name,path in files(source) if name!=MANIFEST and not managed(name)}
    if current!=hashes:raise ValueError('复制期间用户资料发生变化。')
    return len(users)


def extract_package(archive,destination,cancelled=lambda:False):
    archive=Path(archive);destination=Path(destination).resolve()
    if destination.exists():raise ValueError('更新包解压目录已存在。')
    with zipfile.ZipFile(archive) as zipped:
        seen=set();total=0
        if len(zipped.infolist())>200001:raise ValueError('更新包文件过多。')
        for item in zipped.infolist():
            name=item.orig_filename.rstrip('/')
            relative(name)
            root_directory=name=='AutoZZZ' and item.is_dir()
            if item.orig_filename!=item.filename or not (root_directory or name.startswith('AutoZZZ/')):raise ValueError('更新包需要完整的 AutoZZZ 目录。')
            key=name.casefold();mode=item.external_attr>>16
            if key in seen or stat.S_IFMT(mode)not in (0,stat.S_IFREG,stat.S_IFDIR) or item.flag_bits&1:raise ValueError('更新包路径重复或类型无效。')
            seen.add(key);total+=item.file_size
            if total>MAX_PACKAGE_BYTES or item.file_size<0:raise ValueError('更新包展开后超过 8 GB。')
        ancestor=destination.parent
        while not ancestor.exists():ancestor=ancestor.parent
        if shutil.disk_usage(ancestor).free<total+64*1024**2:raise ValueError('解压目录可用空间不足。')
        destination.mkdir(parents=True)
        for item in zipped.infolist():
            if cancelled():raise InterruptedError('已取消更新包解压。')
            target=filesystem_path(destination/item.filename)
            if item.is_dir():target.mkdir(parents=True,exist_ok=True);continue
            target.parent.mkdir(parents=True,exist_ok=True)
            with zipped.open(item) as source,target.open('xb') as out:
                while chunk:=source.read(1024*1024):
                    if cancelled():raise InterruptedError('已取消更新包解压。')
                    out.write(chunk)
    return verify_package(destination/'AutoZZZ',strict=True)
