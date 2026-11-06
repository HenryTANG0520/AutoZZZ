"""Bounded SDK snapshots and source archives; no mutable profile is replaced in place."""
import json
import os
from pathlib import Path
import shutil
import tarfile
import uuid

from .onedragon_bundle import digest,tree_files,relative_name,filesystem_path
from .runtime_selection import plain,tree_hash

DATA_PARTS=('config','assets','plugins','plugin_data','autozzz/dependencies')


def atomic_bytes(path,raw):
    path=filesystem_path(plain(path));path.parent.mkdir(parents=True,exist_ok=True)
    temporary=plain(path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp'))
    try:
        with temporary.open('xb')as stream:
            stream.write(raw);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:temporary.unlink(missing_ok=True)


def atomic_json(path,value):return atomic_bytes(path,json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2).encode())


def data_files(root):
    root=plain(root);result={}
    for part in DATA_PARTS:
        directory=plain(root/part)
        if directory.exists():
            for name,path in tree_files(directory):result[part+'/'+name]=path
    return result


def data_state(root):
    return {name:digest(path)for name,path in data_files(root).items()}


def resource_files(root):
    root=plain(root);result={}
    for part in ('config','assets'):
        directory=plain(root/part)
        if directory.exists():
            result.update({part+'/'+name:path for name,path in tree_files(directory)})
    return result


def pending_edits(root):
    root=filesystem_path(Path(root))
    config=plain(root/'config')
    if not config.exists():return []
    result=[]
    for pattern in ('autozzz*transaction*.json','autozzz*pending*.json','autozzz-devtools-edit.json'):
        result.extend(path.relative_to(root).as_posix()for path in config.rglob(pattern)if path.is_file())
    return sorted(set(result))


def copy_files(root,target,files,cancelled):
    root,target=plain(root),plain(target)
    for name in files:
        if cancelled.is_set():raise InterruptedError('SDK构建已取消，候选与资料快照保留。')
        relative_name(name)
        origin=filesystem_path(plain(root/name))
        destination=filesystem_path(plain(target/name))
        if not origin.is_file():raise ValueError('SDK来源文件缺失：'+name)
        destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(origin,destination)


def unpack_archive(archive,destination,cancelled):
    destination=plain(destination);destination.mkdir(parents=True,exist_ok=False);count=total=0;seen=set()
    with tarfile.open(archive,'r:')as stream:
        for member in stream:
            if cancelled.is_set():raise InterruptedError('源码导出已取消，候选保留。')
            name=member.name.rstrip('/');relative_name(name)
            if name.casefold()in seen:raise ValueError('源码导出包含大小写重复路径。')
            seen.add(name.casefold());target=plain(destination/name)
            if not target.is_relative_to(destination)or name.partition('/')[0]=='.git':raise ValueError('源码导出路径越界。')
            if member.isdir():target.mkdir(parents=True,exist_ok=True);continue
            if not member.isfile():raise ValueError('源码导出含链接或特殊文件，未执行。')
            count+=1;total+=member.size
            if count>50000 or total>2*1024**3 or member.size>512*1024**2:raise ValueError('源码导出超过大小或文件数限制。')
            target.parent.mkdir(parents=True,exist_ok=True)
            with stream.extractfile(member)as source,target.open('xb')as output:shutil.copyfileobj(source,output)
    return dict(files=count,bytes=total)


def copy_base_python(base,target,cancelled):
    # uv-managed stable interpreters use a version alias junction. Copy its verified physical base, never reproduce the alias.
    base,target=plain(Path(base).resolve()),plain(target);target.mkdir(parents=True,exist_ok=False)
    files={name:path for name,path in tree_files(base)if not name.casefold().startswith(('lib/site-packages/','scripts/'))}
    copy_files(base,target,files,cancelled)
    if not all((target/name).is_file()for name in ('python.exe','python311.dll')):raise ValueError('来源不是完整可移动Python3.11。')
    return len(files)


def referenced_archives(root,pending):
    root=plain(root);result={}
    for name in pending:
        marker=json.loads(plain(root/name).read_text(encoding='utf-8'))
        if not isinstance(marker,dict):raise ValueError('待核对事务格式无效，未丢弃：'+name)
        group='devtools'if 'devtools'in name else'agent-templates'if 'agent-template'in name else'maps'if 'map-transaction'in name else None
        if group is None:raise ValueError('尚未适配该待核对事务的资料携带：'+name)
        archive_ids=[marker.get('id')or marker.get('snapshot')]
        if group=='devtools'and marker.get('snapshot'):
            snapshot=marker['snapshot'];directory=plain(root/'archives/devtools')
            if directory.is_dir():
                for child in directory.iterdir():
                    manifest=child/'manifest.json'
                    if not manifest.is_file():continue
                    data=json.loads(manifest.read_text(encoding='utf-8'))
                    if data.get('metadata',{}).get('edit_id')==snapshot:archive_ids.append(child.name)
            archive_ids.extend(marker.get('remaining')or [])
        for archive_id in set(archive_ids):
            from .runtime_selection import identifier
            directory=plain(root/'archives'/group/identifier(archive_id))
            if not directory.is_dir():raise ValueError('待核对事务归档缺失，未丢弃：'+name)
            for relative,path in tree_files(directory):result[directory.relative_to(root).as_posix()+'/'+relative]=path
    return result


def snapshot_data(source,target,new_source,old_source,revision,protocol,cancelled,expected,*,
                  allow_pending=False,old_defaults=None):
    """Carry user overrides and refresh defaults using verified old resource bytes or hashes."""
    source,target,new_source=map(plain,(source,target,new_source))
    if old_defaults is None:old_source=plain(old_source)
    if target.exists():raise ValueError('SDK资料快照已存在，未覆盖。')
    before=data_state(source)
    if before!=expected:raise ValueError('当前账号资料在审核后发生变化，请重新审核SDK候选。')
    issues=pending_edits(source)
    if issues and not allow_pending:raise ValueError('当前资料存在待恢复修改，未升级或丢弃：'+'、'.join(issues))
    carried=referenced_archives(source,issues)if issues else {}
    archive_hashes={name:digest(path)for name,path in carried.items()}
    target.mkdir(parents=True);copy_files(source,target,before,cancelled)
    if carried:copy_files(source,target,carried,cancelled)
    new_resources=resource_files(new_source)
    if old_defaults is None:
        old_resources=resource_files(old_source)
        old_defaults={name:digest(old_resources[name]) for name in old_resources.keys() & new_resources.keys() & before.keys()}
    changed=[];preserved=[]
    for name,origin in new_resources.items():
        if issues:continue  # Pending transaction versions must remain byte-for-byte unchanged.
        destination=filesystem_path(plain(target/name));exists=destination.exists()
        old_digest=old_defaults.get(name)
        unchanged_builtin=old_digest is not None and before.get(name)==old_digest
        if not exists or unchanged_builtin:
            if not exists or digest(origin)!=before.get(name):
                destination.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(filesystem_path(origin),destination);changed.append(name)
        elif old_digest is not None and digest(origin)!=old_digest:preserved.append(name)
    # Obsolete defaults remain as user-reviewable material; no automatic deletion of user assets.
    if data_state(source)!=before or any(digest(source/name)!=checksum for name,checksum in archive_hashes.items()):raise ValueError('资料快照期间原账号或待恢复归档发生变化，已保留未激活候选。')
    atomic_json(target/'autozzz-runtime.json',dict(revision=revision,protocol=protocol))
    return dict(source=str(source),source_hash=tree_hash(before),files=len(before),refreshed_defaults=changed,
        preserved_modified_resources=preserved,snapshot_hash=tree_hash(data_state(target)),carried_pending=issues,carried_archive_files=len(carried),historical_archives=str(source/'archives'))
