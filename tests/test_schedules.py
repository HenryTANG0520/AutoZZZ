from dataclasses import replace
from pathlib import Path
import json
import os
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from autozzz.schedules import Schedule,ScheduleStore,revision
from autozzz.windows_schedules import NAMESPACE,task_xml,WindowsSchedules


class ScheduleTests(unittest.TestCase):
    def test_default_plan_is_disabled_and_enable_requires_review(self):
        plan=Schedule();plan.validate();self.assertFalse(plan.enabled)
        with self.assertRaises(ValueError):replace(plan,enabled=True).validate()
        replace(plan,enabled=True,authorization={'configuration':'reviewed'}).validate()

    def test_strict_values_do_not_coerce_switches_dates_days_or_budgets(self):
        plan=Schedule()
        for values in ({'id':'../other'},{'enabled':1},{'time':'9:00'},{'time':'24:00'},
            {'trigger':'once'},{'date':'2026-02-30'},{'date':'2026-1-01'},
            {'weekdays':[True]},{'weekdays':[0,0]},{'weekdays':[7]},
            {'mode':'native','instance':None},{'instance':True},{'server':'other'},
            {'name':'name\nscript'},{'mode':'daily','allowance':20,'battles':1,'maximum':20},
            {'mode':'native','instance':1,'allowance':20,'battles':None,'maximum':20}):
            with self.subTest(values=values),self.assertRaises(ValueError):replace(plan,**values).validate()

    def test_large_explicit_budget_is_preserved_without_increasing_existing_game_authorization(self):
        plan=Schedule(mode='native',instance=1,allowance=2400,battles=120,maximum=100,timeout=86400)
        restored=Schedule.load(plan.dump())
        self.assertEqual((restored.allowance,restored.battles,restored.maximum,restored.timeout),(2400,120,100,86400))
        self.assertFalse(restored.enabled)

    def test_store_read_is_side_effect_free_and_installations_are_isolated(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);store=ScheduleStore(root/'one',root/'profile/plans.json')
            data=store.read();self.assertFalse(store.path.exists());self.assertEqual(data['plans'],[])
            self.assertNotEqual(store.owner,ScheduleStore(root/'two',store.path).owner)
            plan=Schedule(name='中文 空格 & "符号"');saved=store.put(plan,revision(data))
            self.assertEqual(store.get(plan.id).name,plan.name)
            with self.assertRaises(ValueError):ScheduleStore(root/'two',store.path).read()
            self.assertEqual(len(saved['plans']),1)

    def test_conflicting_write_or_remove_never_replaces_current_plan(self):
        with tempfile.TemporaryDirectory() as folder:
            store=ScheduleStore(Path(folder),Path(folder)/'plans.json');before=store.read()
            plan=Schedule();current=store.put(plan,revision(before));raw=store.path.read_bytes()
            with self.assertRaises(ValueError):store.put(replace(plan,name='changed'),revision(before))
            with self.assertRaises(ValueError):store.remove(plan.id,revision(before))
            self.assertEqual(store.path.read_bytes(),raw)
            self.assertEqual(store.remove(plan.id,revision(current))['plans'],[])

    def test_plan_revision_changes_with_execution_permission_and_configuration(self):
        plan=Schedule()
        for changed in (replace(plan,time='10:00'),replace(plan,server='global'),
                        replace(plan,authorization={'snapshot':'next'}),replace(plan,highest=False)):
            self.assertNotEqual(changed.fingerprint,plan.fingerprint)
        self.assertEqual(replace(plan,binding_error='temporary error').fingerprint,plan.fingerprint)

    def test_failed_atomic_replace_preserves_current_plan_and_removes_own_temporary(self):
        with tempfile.TemporaryDirectory() as folder:
            store=ScheduleStore(Path(folder),Path(folder)/'plans.json');plan=Schedule();current=store.put(plan,revision(store.read()));before=store.path.read_bytes()
            with patch('autozzz.schedules.os.replace',side_effect=OSError('fixture write failure')),self.assertRaises(OSError):
                store.put(replace(plan,name='next'),revision(current))
            self.assertEqual(store.path.read_bytes(),before);self.assertEqual(list(Path(folder).glob('*.tmp')),[])

    def test_hard_link_plan_is_not_followed_or_replaced(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'shared.json';source.write_text('private outside data');os.link(source,root/'plans.json')
            store=ScheduleStore(root,root/'plans.json')
            with self.assertRaises(ValueError):store.read()
            self.assertEqual(source.read_text(),'private outside data')

    def test_windows_request_is_json_data_and_response_errors_do_not_repeat_mutation(self):
        manager=WindowsSchedules('a'*32,Path(sys.executable),Path.cwd())
        seen=[]
        def invoke(command,**options):
            self.assertIsInstance(command,list);self.assertNotIn('shell',options)
            path=Path(command[command.index('-InputPath')+1]);seen.append(json.loads(path.read_text(encoding='utf-8')))
            return NS(stdout='not json',stderr='specific failure',returncode=1)
        with patch('autozzz.windows_schedules.subprocess.run',side_effect=invoke) as called,self.assertRaisesRegex(RuntimeError,'specific failure'):
            manager.call('register',key='b'*32,xml='<literal>"$() & 中文</literal>')
        self.assertEqual(called.call_count,1);self.assertEqual(seen[0]['xml'],'<literal>"$() & 中文</literal>')
        for raw in ('[]','null','{"ok":1}'):
            with patch('autozzz.windows_schedules.subprocess.run',return_value=NS(stdout=raw,stderr='',returncode=0)),self.assertRaises(RuntimeError):manager.call('list')

    def test_all_trigger_xml_and_execution_settings_match_the_plan(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);command=root/'程序 & 符号.exe';command.touch()
            for kind in ('once','daily','weekly','logon'):
                with self.subTest(kind=kind):
                    plan=Schedule(name='计划 & <名称>',trigger=kind,date='2099-01-02',time='05:06',weekdays=[0,2,6],highest=False)
                    xml=task_xml(plan,'a'*32,command,['--path',r'C:\中文 空格\计划.json','--quoted','one"two'],root)
                    document=ET.fromstring(xml);ns={'t':NAMESPACE}
                    def read(path):return document.find('t:'+path.replace('/','/t:'),ns).text
                    self.assertEqual(read('RegistrationInfo/Description'),plan.name)
                    self.assertEqual(read('Principals/Principal/LogonType'),'InteractiveToken')
                    self.assertEqual(read('Principals/Principal/RunLevel'),'LeastPrivilege')
                    self.assertEqual(read('Settings/MultipleInstancesPolicy'),'IgnoreNew')
                    self.assertEqual(read('Settings/AllowHardTerminate'),'false')
                    self.assertEqual(read('Settings/ExecutionTimeLimit'),'PT0S')
                    self.assertEqual(read('Settings/Enabled'),'false')
                    self.assertEqual(read('Settings/WakeToRun'),'false')
                    self.assertEqual(read('Settings/StartWhenAvailable'),'false')
                    self.assertEqual(read('Actions/Exec/Command'),str(command.resolve()))
                    self.assertIn('"C:\\中文 空格\\计划.json"',read('Actions/Exec/Arguments'))
                    if kind=='weekly':
                        days=document.find('t:Triggers/t:CalendarTrigger/t:ScheduleByWeek/t:DaysOfWeek',ns)
                        self.assertEqual([element.tag.rsplit('}',1)[1] for element in days],['Monday','Wednesday','Sunday'])
                    elif kind=='once':self.assertEqual(read('Triggers/TimeTrigger/StartBoundary'),'2099-01-02T05:06:00')
                    elif kind=='logon':self.assertEqual(read('Triggers/LogonTrigger/UserId'),'CURRENT_USER_SID')


if __name__=='__main__':unittest.main()
