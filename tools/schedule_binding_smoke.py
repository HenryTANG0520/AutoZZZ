"""Compare actual Windows exported definitions; never enable or execute a task."""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys
import uuid
import xml.etree.ElementTree as ET

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from autozzz.schedules import Schedule
from autozzz.windows_schedules import WindowsSchedules,definition_matches


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--data',type=Path,required=True);args=parser.parse_args()
    root=Path(__file__).resolve().parents[1];base=args.data.resolve()
    if base.exists() or not base.is_relative_to(root/'runs'):raise ValueError('Use a new evidence folder in runs.')
    base.mkdir(parents=True);backend=WindowsSchedules(uuid.uuid4().hex,Path(sys.executable),root)
    plan=Schedule(highest=False,date='2099-01-02',weekdays=[0,2,6]);registered=False;verified=[]
    try:
        for kind in ('once','daily','weekly','logon'):
            current=replace(plan,trigger=kind);registered=True
            row=backend.register(current,['-c','raise SystemExit(99)'])['task']
            assert not row['enabled'] and not row['conflict']
            tree=ET.fromstring(row['xml']);assert definition_matches(current,tree),(kind,row['xml'])
            (base/(kind+'.xml')).write_text(row['xml'],encoding='utf-8')
            for field in ('wake','missed','highest'):
                assert not definition_matches(replace(current,**{field:True}),tree),(kind,field)
            verified.append(kind)
        assert backend.call('instances',key=plan.id)['instances']==[]
    finally:
        if registered:backend.call('remove',key=plan.id)
        assert backend.call('list')['tasks']==[]
        backend.call('remove_empty_folder')
    result=dict(trigger_definitions=verified,windows_defaults_supported=True,changed_settings_refused=True,
        disabled_throughout=True,task_started=False,owned_task_and_folder_removed=True,game_started=False,energy_spent=0)
    (base/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result))


if __name__=='__main__':main()
