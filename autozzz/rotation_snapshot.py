"""轮换资料快照；只接受本工作进程通过受控保存产生的版本推进。"""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path,PurePosixPath
import re
import threading


def tracked(name):
    parts=PurePosixPath(name).parts
    return bool(parts and (parts[0]=='config' and name.endswith('.yml') or
                parts[:3]==('assets','game_data','world_patrol') and name.endswith(('.png','.yml'))))


def snapshot(root):
    root=Path(root).resolve();files=list((root/'config').rglob('*.yml'))
    files.extend(path for path in (root/'assets/game_data/world_patrol').rglob('*') if path.is_file() and path.suffix in ('.png','.yml'))
    if len(files)>20000:raise ValueError('轮换资料文件过多。')
    result={};total=0
    for path in sorted(files):
        if path.resolve()!=path.absolute() or not path.resolve().is_relative_to(root) or path.stat().st_nlink>1:
            raise ValueError('轮换资料包含重定向或共享硬链接。')
        raw=path.read_bytes();total+=len(raw)
        if total>256*1024*1024:raise ValueError('轮换资料过大。')
        result[path.relative_to(root).as_posix()]=hashlib.sha256(raw).hexdigest()
    return result


class RotationSnapshot:
    def __init__(self,root,expected):
        self.root=Path(root).resolve();self.expected={};self.failed=False;self.lock=threading.RLock();self.originals=[]
        if not isinstance(expected,dict) or len(expected)>20000:raise ValueError('轮换快照格式无效。')
        for name,digest in expected.items():
            if not isinstance(name,str) or not tracked(name) or '\\' in name or PurePosixPath(name).as_posix()!=name or any(part in ('..','.') for part in PurePosixPath(name).parts):
                raise ValueError('轮换快照路径无效。')
            if not isinstance(digest,str) or re.fullmatch('[0-9a-f]{64}',digest) is None:raise ValueError('轮换快照版本无效。')
            self.expected[name]=digest
        self.check()

    def check(self):
        with self.lock:
            if self.failed:raise ValueError('轮换资料快照已失效，请重新预检。')
            try:current=snapshot(self.root)
            except BaseException:self.failed=True;raise
            if current!=self.expected:
                self.failed=True;raise ValueError('轮换资料被外部修改，请重新预检。')

    def checkpoint(self):
        with self.lock:self.check();return copy.deepcopy(self.expected)

    def authorized(self,path,action):
        path=Path(path).absolute();name=path.relative_to(self.root).as_posix()
        if not tracked(name):return action()
        with self.lock:
            if path.resolve()!=path or not path.resolve().is_relative_to(self.root):
                self.failed=True;raise ValueError('轮换保存路径被重定向。')
            self.check()
            try:
                result=action();actual=snapshot(self.root)
                expected=dict(self.expected)
                if name in actual:expected[name]=actual[name]
                else:expected.pop(name,None)
                if actual!=expected:raise ValueError('保存期间存在其他轮换资料修改。')
                self.expected=expected;return result
            except BaseException:self.failed=True;raise

    def install(self):
        from one_dragon.base.config.yaml_operator import YamlOperator
        from functools import wraps
        for name in ('save','save_diy'):
            original=getattr(YamlOperator,name)
            def factory(original):
                @wraps(original)
                def save(config,*args,**kwargs):
                    path=config._get_write_path()
                    if path is None:return original(config,*args,**kwargs)
                    target=Path(path).absolute()
                    if not target.is_relative_to(self.root) or target.resolve()!=target or target.exists() and target.stat().st_nlink>1:
                        self.failed=True;raise ValueError('轮换配置写入路径不在独立数据目录或已重定向。')
                    if not tracked(target.relative_to(self.root).as_posix()):return original(config,*args,**kwargs)
                    return self.authorized(target,lambda:original(config,*args,**kwargs))
                return save
            self.originals.append((YamlOperator,name,original));setattr(YamlOperator,name,factory(original))

    def close(self):
        for cls,name,original in reversed(self.originals):setattr(cls,name,original)
        self.originals.clear()
