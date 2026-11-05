"""Remove custom conditional policies without discarding their original YAML."""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

from .run_lock import profile_lock


def checksum(raw):return hashlib.sha256(raw).hexdigest()


class PolicyArchives:
    def __init__(self,service):
        self.service=service;self.root=Path(service.data).resolve()

    def inside(self,path):
        path=Path(path)
        if not path.resolve().is_relative_to(self.root):raise ValueError('策略路径超出资料目录。')
        current=path
        while current!=self.root:
            if current.exists() and (current.is_symlink() or getattr(current.lstat(),'st_file_attributes',0)&0x400):
                raise ValueError('策略资料不能是链接。')
            current=current.parent
        return path

    def path(self,name,kind):
        self.service._name(name);self.service._policy_kind(kind)
        # Windows aliases upper-case suffixes to the same built-in sample/merged file.
        if name.casefold().endswith(('.sample','.merged')):
            raise ValueError('内置策略后缀不能作为自定义策略名。')
        return self.inside(self.root/'config'/kind/(name+'.yml'))

    def raw(self,path):
        self.inside(path)
        if not path.is_file() or path.stat().st_size>1024**2:raise ValueError('策略文件缺失或超过1MiB。')
        return path.read_bytes()

    def references(self,name,kind):
        import yaml
        keys={'auto_battle','auto_battle_config'} if kind=='auto_battle' else {'dodge_assistant_config','dodge_config'}
        identity=os.path.normcase(name)
        found=[]
        def visit(value):
            if isinstance(value,dict):
                return any((key in keys and isinstance(item,str) and os.path.normcase(item)==identity) or visit(item) for key,item in value.items())
            if isinstance(value,list):return any(visit(item) for item in value)
            return False
        for path in (self.root/'config').rglob('*.yml'):
            relative=path.relative_to(self.root/'config')
            if relative.parts[0] in ('auto_battle','dodge','auto_battle_operation','auto_battle_state_handler'):continue
            try:
                if visit(yaml.safe_load(self.raw(path))):found.append(relative.as_posix())
            except (OSError,ValueError,yaml.YAMLError) as exc:
                raise ValueError('无法核对策略引用：'+relative.as_posix()) from exc
        return found

    def remove(self,name,kind,revision):
        self.service._assert_idle()
        with profile_lock(self.root):
            path=self.path(name,kind)
            if any(self.inside(path.with_name(name+suffix)).exists() for suffix in ('.sample.yml','.merged.yml')):
                raise ValueError('内置策略不能删除；请另存自定义策略。')
            raw=self.raw(path)
            if not isinstance(revision,str) or checksum(raw)!=revision:raise ValueError('策略已变化，请重新读取。')
            refs=self.references(name,kind)
            if refs:raise ValueError('策略仍被引用，请先更换：'+'、'.join(refs))
            identifier=uuid.uuid4().hex
            directory=self.inside(self.root/'autozzz/policy-archives'/identifier)
            directory.mkdir(parents=True,exist_ok=False)
            manifest=dict(name=name,kind=kind,sha256=checksum(raw),created_at=datetime.now().astimezone().isoformat())
            (directory/'policy.yml').write_bytes(raw)
            (directory/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False),encoding='utf-8')
            if self.raw(path)!=raw or self.references(name,kind):raise ValueError('归档期间策略或引用变化，原件保留。')
            path.unlink()
            return dict(archive_id=identifier,name=name,kind=kind)

    def restore(self,identifier):
        self.service._assert_idle()
        if not isinstance(identifier,str) or re.fullmatch('[a-f0-9]{32}',identifier) is None:raise ValueError('策略归档编号无效。')
        with profile_lock(self.root):
            directory=self.inside(self.root/'autozzz/policy-archives'/identifier)
            manifest=json.loads(self.raw(directory/'manifest.json'))
            if not isinstance(manifest,dict) or set(manifest)!={'name','kind','sha256','created_at'}:raise ValueError('策略归档无效。')
            target=self.path(manifest['name'],manifest['kind']);raw=self.raw(directory/'policy.yml')
            if checksum(raw)!=manifest['sha256']:raise ValueError('策略归档内容已变化。')
            if any(self.inside(target.with_name(manifest['name']+suffix)).exists() for suffix in ('.yml','.sample.yml','.merged.yml')):
                raise ValueError('同名策略已存在，恢复不会覆盖。')
            temporary=target.with_name('autozzz-check-'+uuid.uuid4().hex+'.yml')
            try:
                target.parent.mkdir(parents=True,exist_ok=True)
                temporary.write_bytes(raw)
                os.link(temporary,target)  # Exclusive publication, including external races.
            finally:temporary.unlink(missing_ok=True)
            return dict(name=manifest['name'],kind=manifest['kind'],content=raw.decode('utf-8'),revision=checksum(raw))
