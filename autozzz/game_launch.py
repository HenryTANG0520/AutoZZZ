"""Shared argv launcher and recoverable per-game Auto HDR preferences.

The default never changes Windows display preferences. Registry transactions
are outside SDK snapshots so switching SDK/account cannot hide pending work.
"""
from __future__ import annotations

import base64
import ctypes
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import uuid

from .models import AutomationError
from .run_lock import RunLock


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def windows_arguments(text):
    """Parse Windows quoting with the Windows API, never through a shell."""
    if not isinstance(text, str) or '\0' in text or len(text)>32700:
        raise ValueError('高级启动参数无效或过长。')
    shell=ctypes.WinDLL('shell32',use_last_error=True)
    shell.CommandLineToArgvW.argtypes=[ctypes.c_wchar_p,ctypes.POINTER(ctypes.c_int)]
    shell.CommandLineToArgvW.restype=ctypes.POINTER(ctypes.c_wchar_p)
    count=ctypes.c_int();pointer=shell.CommandLineToArgvW('AutoZZZ.exe '+text,ctypes.byref(count))
    if not pointer:raise ValueError('无法解析高级启动参数。')
    try:return [pointer[index] for index in range(1,count.value)]
    finally:
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.LocalFree.argtypes=[ctypes.c_void_p];kernel.LocalFree(pointer)


@dataclass(frozen=True)
class LaunchOptions:
    launch_argument: bool=False
    screen_size: str='1920x1080'
    full_screen: str='0'
    popup_window: bool=False
    monitor: str='1'
    launch_argument_advance: str=''
    autozzz_temporary_hdr: bool=False

    def __post_init__(self):
        for name in ('launch_argument','popup_window','autozzz_temporary_hdr'):
            if type(getattr(self,name))is not bool:raise ValueError(name+' 须为开关值。')
        if self.screen_size not in ('1920x1080','2560x1440','3840x2160'):raise ValueError('窗口尺寸无效。')
        if self.full_screen not in ('0','1')or self.monitor not in ('1','2','3','4'):raise ValueError('全屏或显示器序号无效。')
        windows_arguments(self.launch_argument_advance)

    @classmethod
    def from_mapping(cls,values=None):
        if values is None:return cls()
        if not isinstance(values,dict)or set(values)-set(cls.__dataclass_fields__):raise ValueError('启动配置包含未知字段。')
        return cls(**values)

    @classmethod
    def from_config(cls,config):
        defaults=cls()
        return cls(**{name:config.get(name,getattr(defaults,name))for name in cls.__dataclass_fields__})

    def to_dict(self):return asdict(self)

    def argv(self,path):
        result=[str(path)]
        if self.launch_argument:
            width,height=self.screen_size.split('x')
            result+=windows_arguments(self.launch_argument_advance)
            result+=['-screen-width',width,'-screen-height',height,'-screen-fullscreen',self.full_screen]
            if self.popup_window:result+=['-popupwindow']
            result+=['-monitor',self.monitor]
        return result


def _encode(value):
    return {'bytes':base64.b64encode(value).decode('ascii')}if isinstance(value,bytes)else value


def _decode(value):
    if isinstance(value,dict):
        if set(value)!={'bytes'}:raise ValueError('HDR 注册表记录无效。')
        return base64.b64decode(value['bytes'],validate=True)
    return value


class WindowsRegistry:
    KEY=r'Software\Microsoft\DirectX\UserGpuPreferences'
    def read(self,path):
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,self.KEY,0,winreg.KEY_READ)as key:
                value,kind=winreg.QueryValueEx(key,path)
                return dict(exists=True,type=kind,data=_encode(value))
        except FileNotFoundError:return dict(exists=False)

    def write(self,path,value):
        import winreg
        if value['exists']:
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,self.KEY,0,winreg.KEY_WRITE)as key:
                winreg.SetValueEx(key,path,0,value['type'],_decode(value['data']))
        else:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER,self.KEY,0,winreg.KEY_WRITE)as key:winreg.DeleteValue(key,path)
            except FileNotFoundError:pass


def hdr_value(before,enabled):
    # Preserve GPU preference entries instead of erasing the whole REG_SZ.
    if before['exists']and(before.get('type')not in (1,2)or not isinstance(before.get('data'),str)):
        raise ValueError('该游戏的 GPU 偏好不是文本类型，未修改；请先检查系统设置。')
    text=before.get('data','')
    pieces=[piece for piece in text.split(';')if piece and not piece.strip().casefold().startswith('autohdrenable=')]
    pieces.append('AutoHDREnable='+('2097'if enabled else'2096'))
    return dict(exists=True,type=before.get('type',1),data=';'.join(pieces)+';')


class HdrStore:
    def __init__(self,path,*,root=None,registry=None,guard=None,lock_factory=RunLock):
        self.path=str(Path(path).resolve());self.registry=registry or WindowsRegistry();self.guard=guard or(lambda:None)
        self.root=Path(root)if root is not None else Path(os.environ.get('LOCALAPPDATA',str(Path.home()/'.local/share')))/'AutoZZZ/LaunchHDR'
        self.key=hashlib.sha256(self.path.casefold().encode()).hexdigest()
        self.active=self.root/(self.key+'.json');self.lock_factory=lock_factory

    def lock(self):return self.lock_factory('Local\\AutoZZZ.LaunchHDR.'+self.key)

    def _safe(self):
        self.root.mkdir(parents=True,exist_ok=True)
        if self.root.resolve()!=self.root.absolute()or self.root.is_symlink():raise ValueError('HDR 事务目录不能是重定向目录。')
        if self.active.is_symlink()or self.active.exists()and self.active.resolve().parent!=self.root.resolve():raise ValueError('HDR 事务路径无效。')

    def _read(self):
        self._safe()
        if not self.active.exists():return None,None
        raw=self.active.read_bytes()
        if len(raw)>65536:raise ValueError('HDR 中断记录过大。')
        row=json.loads(raw)
        if (not isinstance(row,dict)or set(row)!={'format','id','path','before','after','kind'}or row['format']!=1
            or row['path']!=self.path or not re.fullmatch('[a-f0-9]{32}',row['id'])or row['kind']not in ('temporary','manual')):
            raise ValueError('HDR 中断记录无效；保留原文件，请检查事务目录。')
        for name in ('before','after'):
            value=row[name]
            if not isinstance(value,dict)or type(value.get('exists'))is not bool:raise ValueError('HDR 原值记录无效。')
            if value['exists']:
                if set(value)!={'exists','type','data'}or type(value['type'])is not int:raise ValueError('HDR 原值类型无效。')
                _decode(value['data'])
            elif set(value)!={'exists'}:raise ValueError('HDR 缺失原值记录无效。')
        return row,hashlib.sha256(raw).hexdigest()

    def _save(self,row):
        self._safe()
        if self.active.exists():raise ValueError('有尚未恢复的 HDR 修改，请先核对恢复。')
        raw=json.dumps(row,ensure_ascii=False,indent=2).encode()
        temporary=self.root/(self.key+'.'+uuid.uuid4().hex+'.tmp')
        try:
            with temporary.open('xb')as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
            # Atomic and exclusive publication: a partial journal can never
            # precede a registry write or replace somebody else's transaction.
            os.link(temporary,self.active)
        finally:temporary.unlink(missing_ok=True)
        return hashlib.sha256(raw).hexdigest()

    def _write_known(self,before,after,journal):
        self.guard()
        if self._read()[1]!=journal:raise ValueError('HDR 事务文件已变化，未写入注册表。')
        if self.registry.read(self.path)!=before:raise ValueError('系统 GPU 偏好已变化，未覆盖。')
        self.registry.write(self.path,after)
        if self.registry.read(self.path)!=after:raise ValueError('HDR 修改结果未核实；请核对中断记录。')

    def _archive(self,expected):
        row,current=self._read()
        if current!=expected:raise ValueError('HDR 事务文件被外部修改；未覆盖，需重新核对。')
        folder=self.root/'history';folder.mkdir(exist_ok=True)
        if folder.resolve()!=self.root.resolve()/'history':raise ValueError('HDR 归档目录无效。')
        destination=folder/(row['id']+'.json')
        if destination.exists():raise ValueError('HDR 归档已存在，未覆盖。')
        os.rename(self.active,destination)

    def status(self):
        with self.lock():
            row,journal=self._read();value=self.registry.read(self.path)
            return dict(path=self.path,value=value,enabled=('AutoHDREnable=2097;'in value.get('data',''))if isinstance(value.get('data'),str)else None,
                revision=digest(dict(path=self.path,value=value,journal=journal)),journal_digest=journal,pending=row,
                journal_path=str(self.active)if row else None,conflict=bool(row and value not in (row['before'],row['after'])),
                verified_in_game=False)

    def _restore(self,row,journal):
        if self._read()[1]!=journal:raise ValueError('HDR 事务文件已变化，未恢复。')
        current=self.registry.read(self.path)
        if current not in (row['before'],row['after']):raise ValueError('系统 HDR 偏好被外部修改，未覆盖；中断记录已保留。')
        if current!=row['before']:
            self._write_known(current,row['before'],journal)
        self._archive(journal)

    def recover(self,journal_digest,confirmed=False):
        if confirmed is not True:raise ValueError('请明确确认恢复本次 HDR 原值。')
        with self.lock():
            row,current=self._read()
            if row is None or current!=journal_digest:raise ValueError('HDR 中断记录已变化，请重新读取。')
            self._restore(row,current)
        return self.status()

    def set(self,enabled,revision,confirmed=False):
        if type(enabled)is not bool or confirmed is not True:raise ValueError('请明确确认修改该游戏的 Auto HDR 偏好。')
        with self.lock():
            row,journal=self._read();before=self.registry.read(self.path)
            if row is not None:raise ValueError('有尚未恢复的 HDR 修改，请先恢复。')
            if revision!=digest(dict(path=self.path,value=before,journal=journal)):raise ValueError('HDR 状态已变化，请重新读取。')
            after=hdr_value(before,enabled)
            row=dict(format=1,id=uuid.uuid4().hex,path=self.path,before=before,after=after,kind='manual')
            self.guard();journal=self._save(row)
            self._write_known(before,after,journal)
            self._archive(journal)
        return self.status()


class LaunchSession:
    def __init__(self,path,options=None,*,guard=None,hdr_store=None,popen=None,hdr_lease=None):
        self.path=Path(path).resolve();self.options=options if isinstance(options,LaunchOptions)else LaunchOptions.from_mapping(options)
        self.guard=guard or(lambda:None);self.store=hdr_store or HdrStore(self.path,guard=self.guard)
        self.popen=popen or subprocess.Popen;self.hdr_lock=None;self.row=self.journal=None;self.started=False
        self.hdr_lease=hdr_lease

    def check_hdr_lease(self,path):
        if self.path!=path or self.hdr_lock is None or self.journal is None:
            raise ValueError('启动请求没有该游戏的 HDR 事务。')
        if self.store._read()[1]!=self.journal or self.store.registry.read(self.store.path)!=self.row['after']:
            raise ValueError('该游戏的 HDR 事务已被外部修改，请核对后恢复。')

    def begin_hdr(self):
        if self.journal is not None:
            self.check_hdr_lease(self.path)
            return
        self.guard()
        self.hdr_lock=self.store.lock()
        self.hdr_lock.acquire()
        try:
            if self.store._read()[0]is not None:
                raise ValueError('有尚未恢复的 HDR 修改，请先核对恢复。')
            before=self.store.registry.read(self.store.path)
            after=hdr_value(before,False)
            self.row=dict(format=1,id=uuid.uuid4().hex,path=self.store.path,before=before,after=after,kind='temporary')
            self.guard()
            self.journal=self.store._save(self.row)
            self.store._write_known(before,after,self.journal)
        except BaseException:
            self.close()
            raise

    def spawn(self, *, before_spawn=None):
        if self.started:raise AutomationError('同一启动请求不能重复发送。')
        self.guard()
        if self.hdr_lease is None:
            if self.options.autozzz_temporary_hdr:
                self.begin_hdr()
            else:
                # A default start must not launch against somebody else's lease.
                with self.store.lock():
                    if self.store.active.exists() or self.store.active.is_symlink():
                        raise ValueError('有尚未恢复的 HDR 修改，请先核对恢复。')
                    return self._spawn_process(before_spawn)
        try:
            return self._spawn_process(before_spawn)
        except BaseException:
            self.close();raise

    def _spawn_process(self, before_spawn):
        self.guard()
        if self.hdr_lease is not None:
            self.hdr_lease.check_hdr_lease(self.path)
        elif self.journal is not None:
            self.check_hdr_lease(self.path)
        if before_spawn is not None and not before_spawn():
            return None
        self.started=True
        return self.popen(self.options.argv(self.path),cwd=str(self.path.parent),shell=False)

    def close(self):
        try:
            if self.journal is not None:
                if self.hdr_lock is None:
                    self.hdr_lock=self.store.lock();self.hdr_lock.acquire()
                # Restoration is cleanup: stale generation/cancellation must not
                # prohibit returning our own known preference to its original.
                guard=self.store.guard;self.store.guard=lambda:None
                try:self.store._restore(self.row,self.journal);self.journal=None
                finally:self.store.guard=guard
        finally:
            if self.hdr_lock is not None:self.hdr_lock.close();self.hdr_lock=None


def _profile_launch_options(data,instance_idx):
    import yaml
    def read(path):
        if not path.exists():return {}
        if not path.resolve().is_relative_to(data.resolve()):raise ValueError('启动配置指向资料目录之外。')
        value=yaml.safe_load(path.read_bytes())
        if value is not None and not isinstance(value,dict):raise ValueError('启动配置格式无效。')
        return value or{}
    if instance_idx is None:
        rows=read(data/'config/one_dragon.yml').get('instance_list',[])
        active=[row.get('idx')for row in rows if isinstance(row,dict)and row.get('active')is True]
        if len(active)>1:raise ValueError('多个活动账号，无法确定启动配置。')
        if not active:return LaunchOptions()
        instance_idx=active[0]
    if type(instance_idx)is not int or instance_idx<1:raise ValueError('启动账号序号无效。')
    values=read(data/'config'/f'{instance_idx:02d}'/'game.yml')
    return LaunchOptions.from_mapping({name:values[name]for name in LaunchOptions.__dataclass_fields__ if name in values})


def legacy_launch_configuration(root,profile,instance_idx=None):
    """Pin the stable selector before reading options for the independent runner."""
    from .onedragon_runtime import RuntimePaths
    from .runtime_generation import RuntimeGeneration
    stable=RuntimePaths.discover(Path(root),profile,use_selection=False)
    guard=RuntimeGeneration(stable.stable_data or stable.data,stable.generation).check_execution
    # A known pending switch is a transaction, not a broken SDK fallback.
    guard()
    try:
        selected=RuntimePaths.discover(Path(root),profile)
    except (OSError,ValueError,RuntimeError):
        guard()
        return LaunchOptions(),guard,True
    options=_profile_launch_options(selected.data,instance_idx)
    guard()
    return options,guard,False
