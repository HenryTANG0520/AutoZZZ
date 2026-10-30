"""Windows 计划任务适配；JSON 数据与固定 PowerShell 脚本分开传递。"""
from __future__ import annotations

from datetime import date
import json
import os
from pathlib import Path
import subprocess
import tempfile
import xml.etree.ElementTree as ET

from .config import RESOURCE_ROOT
from .schedules import Schedule,identifier

NAMESPACE='http://schemas.microsoft.com/windows/2004/02/mit/task'
DAYS=('Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday')


def task_xml(plan: Schedule,owner: str,command: Path,arguments: list[str],working: Path) -> str:
    plan.validate();identifier(owner)
    command,working=Path(command).resolve(),Path(working).resolve()
    if not command.is_file() or not working.is_dir():raise ValueError('计划启动程序或工作目录不存在。')
    if type(arguments)is not list or any(type(value)is not str or '\0'in value or '\r'in value or '\n'in value for value in arguments):
        raise ValueError('计划启动参数无效。')
    ET.register_namespace('',NAMESPACE)
    def add(parent: ET.Element,name: str,value: str|None=None,**attributes: str) -> ET.Element:
        element=ET.SubElement(parent,'{'+NAMESPACE+'}'+name,attributes)
        if value is not None:element.text=value
        return element
    root=ET.Element('{'+NAMESPACE+'}Task',dict(version='1.2'))
    info=add(root,'RegistrationInfo');add(info,'Source','AutoZZZ/'+owner);add(info,'Description',plan.name)
    triggers=add(root,'Triggers')
    trigger=add(triggers,{'once':'TimeTrigger','daily':'CalendarTrigger','weekly':'CalendarTrigger','logon':'LogonTrigger'}[plan.trigger])
    if plan.trigger!='logon':add(trigger,'StartBoundary',(plan.date if plan.trigger=='once' else date.today().isoformat())+'T'+plan.time+':00')
    add(trigger,'Enabled','true')
    if plan.trigger=='daily':add(add(trigger,'ScheduleByDay'),'DaysInterval','1')
    elif plan.trigger=='weekly':
        week=add(trigger,'ScheduleByWeek');add(week,'WeeksInterval','1');days=add(week,'DaysOfWeek')
        for index in sorted(plan.weekdays):add(days,DAYS[index])
    elif plan.trigger=='logon':add(trigger,'UserId','CURRENT_USER_SID')
    principal=add(add(root,'Principals'),'Principal',id='Author');add(principal,'UserId','CURRENT_USER_SID')
    add(principal,'LogonType','InteractiveToken');add(principal,'RunLevel','HighestAvailable' if plan.highest else 'LeastPrivilege')
    settings=add(root,'Settings')
    for name,value in (('MultipleInstancesPolicy','IgnoreNew'),('DisallowStartIfOnBatteries','false'),
        ('StopIfGoingOnBatteries','false'),('AllowHardTerminate','false'),('StartWhenAvailable',str(plan.missed).lower()),
        ('RunOnlyIfNetworkAvailable','false'),('AllowStartOnDemand','true'),('Enabled',str(plan.enabled).lower()),
        ('Hidden','false'),('WakeToRun',str(plan.wake).lower()),('ExecutionTimeLimit','PT0S'),('Priority','7')):
        add(settings,name,value)
    idle=add(settings,'IdleSettings');add(idle,'StopOnIdleEnd','false');add(idle,'RestartOnIdle','false')
    action=add(add(root,'Actions',Context='Author'),'Exec',id='autozzz')
    add(action,'Command',str(command));add(action,'Arguments',subprocess.list2cmdline(arguments));add(action,'WorkingDirectory',str(working))
    return ET.tostring(root,encoding='unicode',xml_declaration=False)


class WindowsSchedules:
    def __init__(self,owner: str,command: Path,working: Path,*,script: Path|None=None):
        self.owner=identifier(owner);self.command=Path(command).resolve();self.working=Path(working).resolve()
        self.script=Path(script) if script is not None else RESOURCE_ROOT/'assets/windows_schedules.ps1'
        self.folder='\\AutoZZZ\\'+self.owner

    def call(self,operation: str,*,key: str|None=None,xml: str|None=None) -> dict:
        if operation not in ('list','register','enable','disable','remove','run','instances','remove_empty_folder'):
            raise ValueError('系统计划操作无效。')
        if operation not in ('list','remove_empty_folder'):identifier(key)
        request=dict(operation=operation,owner=self.owner,folder=self.folder,key=key,command=str(self.command),working=str(self.working),xml=xml)
        if not self.script.is_file():raise ValueError('系统计划适配脚本缺失，请修复完整程序包。')
        shell=Path(os.environ.get('SystemRoot','C:/Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
        with tempfile.TemporaryDirectory(prefix='autozzz-schedule-request-') as directory:
            path=Path(directory)/'request.json';path.write_text(json.dumps(request,ensure_ascii=False),encoding='utf-8')
            result=subprocess.run([str(shell),'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(self.script),'-InputPath',str(path)],
                capture_output=True,encoding='utf-8',errors='replace',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:response=json.loads(result.stdout)
        except (ValueError,TypeError) as exc:
            detail=result.stderr.strip()[:2000]
            raise RuntimeError('系统计划响应无效，请查询原任务；操作不会自动重试。'+(' '+detail if detail else '')) from exc
        if type(response)is not dict or type(response.get('ok'))is not bool:
            raise RuntimeError('系统计划响应格式无效；请查询原任务，操作不会自动重试。')
        if result.returncode or not response['ok']:
            raise RuntimeError(response.get('error') or '系统计划操作失败；请检查当前用户的任务计划权限。')
        return response

    def register(self,plan: Schedule,arguments: list[str]) -> dict:
        return self.call('register',key=plan.id,xml=task_xml(plan,self.owner,self.command,arguments,self.working))
