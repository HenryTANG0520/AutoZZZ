"""定时计划与 Windows 注册协调；旧系统触发不能执行已变更的计划。"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

from .run_lock import profile_lock
from .schedules import Schedule,ScheduleStore,identifier,revision
from .windows_schedules import WindowsSchedules,NAMESPACE


def launcher(root: Path) -> tuple[Path,list[str]]:
    root=Path(root).resolve()
    if (root/'autozzz/__main__.py').is_file():
        command=Path(sys.executable).resolve();windowed=command.with_name('pythonw.exe')
        return (windowed if windowed.is_file() else command),['-m','autozzz']
    command=root/'AutoZZZ.exe'
    if not command.is_file():raise ValueError('当前目录没有可供定时启动的完整程序。')
    return command,[]


class ScheduleManager:
    def __init__(self,store: ScheduleStore,backend=None):
        self.store=store
        self.command,self.prefix=launcher(store.root)
        self.backend=backend or WindowsSchedules(store.owner,self.command,store.root)

    def arguments(self,plan: Schedule) -> list[str]:
        return self.prefix+['--scheduled-run',plan.id,'--schedule-store',str(self.store.path),'--schedule-revision',plan.fingerprint]

    def matches(self,plan: Schedule,system: dict) -> bool:
        if system.get('conflict') or system.get('id')!=plan.id or type(system.get('xml'))is not str:return False
        try:
            tree=ET.fromstring(system['xml']);ns={'t':NAMESPACE}
            def value(path):return tree.find('t:'+path.replace('/','/t:'),ns).text or ''
            return (value('Actions/Exec/Arguments')==subprocess.list2cmdline(self.arguments(plan))
                and value('Actions/Exec/Command').casefold()==str(self.command).casefold()
                and value('Actions/Exec/WorkingDirectory').casefold()==str(self.store.root).casefold()
                and value('RegistrationInfo/Source')=='AutoZZZ/'+self.store.owner
                and type(system.get('enabled'))is bool and system['enabled']==plan.enabled)
        except (ValueError,AttributeError,ET.ParseError):return False

    def catalog(self) -> dict:
        data=self.store.read();system=self.backend.call('list')['tasks'];plans=[]
        for value in data['plans']:
            plan=Schedule.load(value);entry=next((row for row in system if row['id']==plan.id),None)
            plans.append(dict(plan=value,system=entry,bound=bool(entry and self.matches(plan,entry))))
        known={row['id'] for row in data['plans']}
        return dict(revision=revision(data),plans=plans,orphaned=[row for row in system if row['id']not in known],folder=self.backend.folder)

    def _idle(self,key: str) -> dict|None:
        system=self.backend.call('list')['tasks'];entry=next((row for row in system if row['id']==key),None)
        if entry and (entry['state']in (2,4) or self.backend.call('instances',key=key)['instances']):
            raise RuntimeError('原计划仍在运行，请正常停止并等待退出后再修改。')
        return entry

    def save(self,plan: Schedule,expected: str) -> dict:
        plan.validate()
        if plan.enabled:
            from .schedule_authorization import validate_authorization
            validate_authorization(plan)
        with profile_lock(self.store.path.parent/'bindings'):
            if revision(self.store.read())!=expected:raise ValueError('计划清单已变化，请重新读取。')
            self._idle(plan.id)
            current=self.store.put(replace(plan,binding_error=''),expected)
            try:self.backend.register(plan,self.arguments(plan))
            except Exception as exc:
                # 注册可能已发生但响应丢失；撤销本地许可，不重发注册。
                failed=replace(plan,enabled=False,binding_error=str(exc)[:4000])
                self.store.put(failed,revision(current));raise
            return self.catalog()

    def disable(self,key: str,expected: str) -> dict:
        identifier(key)
        with profile_lock(self.store.path.parent/'bindings'):
            current=self.store.read()
            if revision(current)!=expected:raise ValueError('计划清单已变化，请重新读取。')
            plan=self.store.get(key);disabled=replace(plan,enabled=False,binding_error='')
            current=self.store.put(disabled,expected)
            try:self.backend.call('disable',key=key)
            except Exception as exc:
                self.store.put(replace(disabled,binding_error=str(exc)[:4000]),revision(current));raise
            return self.catalog()

    def remove(self,key: str,expected: str) -> dict:
        identifier(key)
        with profile_lock(self.store.path.parent/'bindings'):
            if revision(self.store.read())!=expected:raise ValueError('计划清单已变化，请重新读取。')
            entry=self._idle(key);plan=self.store.get(key)
            if entry is None:self.store.remove(key,expected);return self.catalog()
            current=self.store.put(replace(plan,enabled=False),expected)
            try:self.backend.call('remove',key=key)
            except Exception as exc:
                self.store.put(replace(plan,enabled=False,binding_error=str(exc)[:4000]),revision(current));raise
            self.store.remove(key,revision(current));return self.catalog()

    def run(self,key: str,expected: str) -> dict:
        identifier(key)
        with profile_lock(self.store.path.parent/'bindings'):
            data=self.store.read()
            if revision(data)!=expected:raise ValueError('计划清单已变化，请重新读取。')
            plan=self.store.get(key)
            entry=next((row for row in self.backend.call('list')['tasks'] if row['id']==key),None)
            if not plan.enabled:raise ValueError('请先核对并启用计划。')
            from .schedule_authorization import validate_authorization
            validate_authorization(plan)
            if entry is None or not self.matches(plan,entry):raise ValueError('系统计划与本地配置不一致，请重新保存。')
            return self.backend.call('run',key=key)
