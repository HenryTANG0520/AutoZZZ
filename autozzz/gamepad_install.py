"""Pinned optional gamepad dependency, separate from the immutable SDK bundle.

Archive identity comes from vendor/onedragon/uv.lock (vgamepad 0.1.0).
Never execute its setup.py: it implicitly installs a machine-wide driver.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import struct
import sys
import tarfile
import threading
import time
import uuid

URL='https://files.pythonhosted.org/packages/8a/54/0eaddc33f84247963af078f364b37153d09fcd6cdc398f243ec3e8842c56/vgamepad-0.1.0.tar.gz'
SHA256='57f6bd01aec0c172947517fb782d150ef9b285f7f4d524c317374fa5c24a89de'
FILES={
    'LICENSE':'777ec45017bfb1c95fcb259ef0601e8c59ed40d22512443b4019262b226cf635',
    'vgamepad/__init__.py':'77eb34b1d35c255dff78d1206e1c61400b12048aabddd9da5fa27fb350848d11',
    'vgamepad/lin/__init__.py':'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
    'vgamepad/lin/virtual_gamepad.py':'bab85faea9c3c9604af1c0e2f7f757c9a5d85ebf976fc7be0a7d0906a711d369',
    'vgamepad/win/__init__.py':'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
    'vgamepad/win/vigem/client/x64/ViGEmClient.dll':'2bf0cb1d809039573c922737d298a1653d4dbc61408060ff45a9bcfde82e97d2',
    'vgamepad/win/vigem/client/x86/ViGEmClient.dll':'f5b4bae1f9cf4cdcd746d12101f9c6cf4787f48014d936dc27962c0d82104080',
    'vgamepad/win/vigem/install/x64/ViGEmBusSetup_x64.msi':'5abbba8a4a07aaaeb50b4666183b2f243e0e5ad288026d2a9f3595ed237c4b28',
    'vgamepad/win/vigem/install/x86/ViGEmBusSetup_x86.msi':'e383c1660036fd98719add71aea43fa81818da1a6c7ab9431b4940520a12d1f2',
    'vgamepad/win/vigem_client.py':'9598a035e44740715b11b1ba3faba29b7fd1753bca73dfa1aec0ea514d9b02db',
    'vgamepad/win/vigem_commons.py':'be76cf56fc5d1e07e070c76fe0d0bd846603c89387ec8ff3879713977a68829c',
    'vgamepad/win/virtual_gamepad.py':'aa2211e4a17dcc34244dc5767d79ec61a285e8d47cf744bed1015e9f46ceee53',
}


def dependency_root(data):return Path(data)/'autozzz/dependencies/vgamepad-0.1.0'


def checked(path,root):
    path,root=Path(path),Path(root).resolve()
    if path.resolve()!=root and root not in path.resolve().parents:raise ValueError('手柄依赖路径越界')
    for item in (path,*path.parents):
        if item.exists() and (item.is_symlink() or getattr(item.lstat(),'st_file_attributes',0)&0x400):
            raise ValueError('手柄依赖不能位于链接目录')
        if item==root:break
    return path


def verify_dependency(root):
    root=Path(root)
    if not root.exists():return False
    checked(root,root)
    actual={p.relative_to(root).as_posix() for p in root.rglob('*') if checked(p,root).is_file()}
    if actual!=set(FILES):raise ValueError('手柄依赖有缺失或未知文件，请修复')
    for name,checksum in FILES.items():
        if hashlib.sha256(checked(root/name,root).read_bytes()).hexdigest()!=checksum:
            raise ValueError('手柄依赖摘要不符：'+name)
    return True


def activate_gamepad_support(data):
    """Call before importing pc_button_utils. No device, driver or game is started."""
    root=dependency_root(data)
    if not verify_dependency(root):return False
    # No .pth files, scripts or extra DLLs from the writable parent are loaded.
    # Avoid writing bytecode which would invalidate the exact-file inventory.
    sys.dont_write_bytecode=True
    if str(root) not in sys.path:sys.path.insert(0,str(root))
    return True


def extract_dependency(payload,target):
    if hashlib.sha256(payload).hexdigest()!=SHA256:raise ValueError('虚拟手柄下载摘要不符')
    target=Path(target)
    target.mkdir(parents=True,exist_ok=False)
    with tarfile.open(fileobj=io.BytesIO(payload),mode='r:gz') as archive:
        seen=set()
        for member in archive.getmembers():
            name=member.name
            if member.isdir():continue
            if not name.startswith('vgamepad-0.1.0/'):raise ValueError('依赖压缩包路径无效')
            name=name.removeprefix('vgamepad-0.1.0/')
            if PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts or '\\' in name or not member.isfile():
                raise ValueError('依赖压缩包路径无效')
            if name not in FILES:continue  # setup.py is deliberately never extracted/executed
            if name in seen or member.size>8*1024**2:raise ValueError('依赖压缩包文件无效')
            content=archive.extractfile(member).read()
            if hashlib.sha256(content).hexdigest()!=FILES[name]:raise ValueError('依赖文件摘要不符')
            destination=checked(target/name,target);destination.parent.mkdir(parents=True,exist_ok=True);destination.write_bytes(content);seen.add(name)
    verify_dependency(target)


def driver_state():
    """Read service registration only; game readiness is a separate live check."""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,r'SYSTEM\CurrentControlSet\Services\ViGEmBus') as key:
            start=winreg.QueryValueEx(key,'Start')[0]
        return dict(registered=True,disabled=start==4,game_verified=False)
    except FileNotFoundError:return dict(registered=False,disabled=False,game_verified=False)


def boot_token():
    import ctypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.GetTickCount64.restype=ctypes.c_ulonglong
    return round((time.time()-kernel.GetTickCount64()/1000)/60)


def install_msi(path,repair,log):
    import subprocess
    windows=Path(os.environ.get('SystemRoot','C:/Windows')).resolve()
    command=[str(windows/'System32/msiexec.exe'),'/fa' if repair else '/i',str(path),'/qb','/norestart','/L*v',str(log)]
    # Explicit user action; Windows owns elevation/driver prompts. Never force reboot.
    process=subprocess.Popen(command,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    return process.wait()  # cannot safely cancel an in-progress driver transaction


def _existing_bus_connects(home, arch):
    """Use the verified client DLL to test the bus without attaching a target."""
    import ctypes as C
    from _ctypes import FreeLibrary

    path = checked(home / f'vgamepad/win/vigem/client/{arch}/ViGEmClient.dll', home)
    client = None
    try:
        client = C.CDLL(str(path))
        client.vigem_alloc.argtypes, client.vigem_alloc.restype = [], C.c_void_p
        client.vigem_connect.argtypes, client.vigem_connect.restype = [C.c_void_p], C.c_uint
        client.vigem_disconnect.argtypes, client.vigem_disconnect.restype = [C.c_void_p], None
        client.vigem_free.argtypes, client.vigem_free.restype = [C.c_void_p], None
        handle = client.vigem_alloc()
        if not handle:
            return False
        try:
            if client.vigem_connect(handle) != 0x20000000:  # VIGEM_ERROR_NONE
                return False
            client.vigem_disconnect(handle)
            return True
        finally:
            client.vigem_free(handle)
    except (OSError, AttributeError):
        return False
    finally:
        if client is not None:
            FreeLibrary(client._handle)


class GamepadDependencies:
    def __init__(self,service):
        self.service=service;self.root=Path(service.data);self.thread=None
        self.lock=threading.RLock();self.cancelled=threading.Event();self.job={};self.closed=False

    @property
    def busy(self):return self.thread is not None and self.thread.is_alive()

    def catalog(self):
        self.service._assert_idle()
        try:ready=verify_dependency(dependency_root(self.root));error=''
        except (ValueError,OSError) as exc:ready=False;error=str(exc)
        try:driver=driver_state()
        except OSError as exc:driver=dict(registered=None,disabled=None,game_verified=False,error=str(exc))
        restart=False
        receipt=dependency_root(self.root).parent/'last-install.json'
        if receipt.is_file():
            try:
                checked(receipt,self.root)
                if receipt.stat().st_size>65536:raise ValueError('手柄安装记录超限')
                last=json.loads(receipt.read_text(encoding='utf-8'))
                if (not isinstance(last,dict) or type(last.get('restart_required')) is not bool
                        or type(last.get('boot')) is not int):raise ValueError('手柄安装记录格式无效，请修复')
                restart=last.get('restart_required') is True and last.get('boot')==boot_token()
            except (ValueError,OSError) as exc:error=error or str(exc)
        return dict(version='0.1.0',driver_target_version='1.17.333.0',python_ready=ready,driver=driver,error=error,restart_required=restart,
            revision=hashlib.sha256(json.dumps([ready,driver,error,restart],sort_keys=True).encode()).hexdigest(),
            note='驱动安装或修复可能需要 Windows 管理员确认及重启。游戏输入仍需实测。')

    def start(self,action,revision,confirmed=False):
        if action not in ('install','repair') or confirmed is not True:raise ValueError('请明确确认安装或修复虚拟手柄驱动')
        current=self.catalog()
        if revision!=current['revision']:raise ValueError('手柄依赖状态已变化，请重新检查')
        with self.lock:
            if self.closed:raise RuntimeError('手柄依赖连接已关闭，请重新打开窗口')
            if self.busy:raise RuntimeError('原手柄安装仍在运行')
            self.cancelled.clear();self.job=dict(job_id=uuid.uuid4().hex,state='starting',detail='正在准备虚拟手柄依赖',
                action=action,restart_required=current['restart_required'],process_restart_required=False,installed=False,game_verified=False)
            self.thread=threading.Thread(target=self._work,name='AutoZZZ-GamepadInstall',daemon=False);self.thread.start()
        return self.status()

    def _update(self,**values):
        with self.lock:self.job.update(values)

    def _download(self):
        from .environment_tools import dependency_network,resolve_pinned_archive,read_network
        if self.cancelled.is_set():raise InterruptedError('安装已取消，未运行驱动安装器')
        index,proxy=dependency_network()
        self._update(index_url=index,detail='正在读取所选依赖源：'+index)
        address,hosts=resolve_pinned_archive(index,'vgamepad-0.1.0.tar.gz',SHA256,self.cancelled,proxy=proxy)
        self._update(download_url=address,detail='下载固定归档：'+address)
        payload=read_network(address,self.cancelled,proxy=proxy,limit=4*1024**2,allowed_hosts=hosts,seconds=180)
        if hashlib.sha256(payload).hexdigest()!=SHA256:raise ValueError('虚拟手柄下载摘要不符')
        return payload

    def _work(self):
        from .run_lock import RunLock,profile_lock
        lease=getattr(self.service,'_run_lease',None)
        try:
            with lease.scope() if lease is not None else RunLock():
                with profile_lock(self.root):
                    home=checked(dependency_root(self.root),self.root)
                    self._update(state='downloading',detail='下载并校验固定版本依赖')
                    payload=self._download();stage=home.parent/('stage-'+self.job['job_id'])
                    extract_dependency(payload,stage)
                    if self.cancelled.is_set():raise InterruptedError('安装已取消，未运行驱动安装器')
                    # Replacing importable dependency files invalidates this process
                    # even if post-publication validation or the MSI later fails.
                    self.service._environment_changed=True
                    self._update(process_restart_required=True)
                    if home.exists():home.rename(home.parent/('previous-'+self.job['job_id']))
                    stage.rename(home);verify_dependency(home)
                    arch='x64' if struct.calcsize('P')==8 else 'x86'
                    driver = driver_state() if self.job['action'] == 'install' else {}
                    reuse = driver.get('registered') and not driver.get('disabled') and _existing_bus_connects(home, arch)
                    # Commit the non-cancellable phase under the same lock used by
                    # cancel/close. A successful cancellation before this gate must
                    # never fall through to msiexec, including after final validation.
                    with self.lock:
                        self.job['process_restart_required']=True
                        if self.cancelled.is_set():raise InterruptedError('安装已取消，未运行驱动安装器')
                        if reuse:
                            self.job.update(state='installed_unverified', installed=True, driver=driver, driver_reused=True,
                                            installer_completed=False, detail='固定依赖已准备，沿用已连接的 ViGEmBus；请重新连接助手后验收游戏手柄输入')
                            return
                        self.job.update(state='installing',detail='等待 Windows 驱动安装结束；此阶段不能取消')
                    installer=checked(home/f'vgamepad/win/vigem/install/{arch}/ViGEmBusSetup_{arch}.msi',home)
                    log=home.parent/('driver-'+self.job['job_id']+'.log')
                    code=install_msi(installer,self.job['action']=='repair',log)
                    if code not in (0,3010,1641):raise RuntimeError(f'驱动安装器退出码 {code}，日志：{log}')
                    driver=driver_state()
                    state='restart_required' if code in (3010,1641) else 'installed_unverified'
                    receipt=home.parent/'last-install.json';temporary=receipt.with_suffix('.tmp')
                    temporary.write_text(json.dumps(dict(exit_code=code,restart_required=code in (3010,1641),boot=boot_token()),indent=2),encoding='utf-8')
                    temporary.replace(receipt)
                    self._update(state=state,detail='驱动安装流程已结束；请重新连接助手后检查，游戏手柄输入尚未验收',
                        installed=bool(driver.get('registered') and not driver.get('disabled') and code==0),
                        installer_completed=True,driver=driver,exit_code=code,restart_required=code in (3010,1641),log=str(log))
        except InterruptedError as exc:self._update(state='cancelled',detail=str(exc))
        except Exception as exc:self._update(state='failed',detail=str(exc))

    def status(self,job_id=None):
        with self.lock:
            if job_id is not None and job_id!=self.job.get('job_id'):raise ValueError('虚拟手柄任务编号已变化')
            return dict(self.job,busy=self.busy)

    def cancel(self,job_id):
        with self.lock:
            if job_id!=self.job.get('job_id'):raise ValueError('虚拟手柄任务编号已变化')
            if self.job.get('state')=='installing':raise ValueError('驱动安装进行中，请等待 Windows 安装器退出')
            self.cancelled.set()
        return self.status(job_id)

    def close(self):
        with self.lock:
            self.closed=True
            if self.job.get('state')!='installing':self.cancelled.set()
            worker=self.thread
        if worker is not None:worker.join()
