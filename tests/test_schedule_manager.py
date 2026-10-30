from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

from autozzz.schedule_manager import ScheduleManager
from autozzz.schedules import Schedule,ScheduleStore,revision
from autozzz.windows_schedules import task_xml,NAMESPACE
from autozzz.config import Settings
from autozzz.schedule_authorization import daily_review,authorize


class Backend:
    folder='fixture task folder'
    def __init__(self):self.rows={};self.calls=[];self.fail='';self.manager=None
    def call(self,method,**params):
        self.calls.append((method,params))
        if method==self.fail:raise OSError('fixture system response failure')
        if method=='list':return dict(tasks=list(self.rows.values()))
        key=params['key']
        if method=='instances':return dict(instances=[{'instance':'live'}] if self.rows[key]['state']==4 else [])
        if method=='disable':
            plan=self.manager.store.get(key);self.rows[key].update(enabled=False,xml=task_xml(plan,self.manager.store.owner,self.manager.command,self.manager.arguments(plan),self.manager.store.root))
        elif method=='remove':self.rows.pop(key)
        elif method=='run':return dict(instance='same-instance')
        else:raise AssertionError(method)
        return dict(ok=True)
    def register(self,plan,args):
        self.calls.append(('register',plan.id))
        if self.fail=='register':raise OSError('fixture registration response failure')
        self.rows[plan.id]=dict(id=plan.id,state=3,enabled=plan.enabled,conflict='',xml=task_xml(plan,self.manager.store.owner,self.manager.command,args,self.manager.store.root))


class ScheduleManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        (self.root/'AutoZZZ.exe').touch();self.store=ScheduleStore(self.root,self.root/'profile/plans.json');self.backend=Backend()
        self.manager=ScheduleManager(self.store,self.backend);self.backend.manager=self.manager
        game=self.root/'ZenlessZoneZero.exe';game.touch();Settings(game_path=str(game)).save(self.root/'config.json')
        self.plan=Schedule();self.plan.authorization=authorize(self.plan,daily_review(self.plan,self.root/'config.json'))

    def save(self,plan=None):return self.manager.save(plan or self.plan,revision(self.store.read()))

    def test_save_binds_exact_revision_and_disable_keeps_history_and_running_instance(self):
        result=self.save(replace(self.plan,enabled=True));self.assertTrue(result['plans'][0]['bound'])
        self.backend.rows[self.plan.id]['state']=4
        result=self.manager.disable(self.plan.id,result['revision'])
        self.assertFalse(result['plans'][0]['plan']['enabled']);self.assertTrue(result['plans'][0]['bound'])
        self.assertEqual(self.backend.rows[self.plan.id]['state'],4)
        self.assertNotIn('stop',[call[0] for call in self.backend.calls])

    def test_registration_observation_failure_revokes_local_permission_without_repeating(self):
        self.backend.fail='register'
        with self.assertRaises(OSError):self.save(replace(self.plan,enabled=True))
        saved=self.store.get(self.plan.id);self.assertFalse(saved.enabled);self.assertIn('response failure',saved.binding_error)
        self.assertEqual([call[0] for call in self.backend.calls].count('register'),1)

    def test_running_or_queued_plan_is_rejected_before_local_write(self):
        result=self.save();before=self.store.path.read_bytes()
        for state in (2,4):
            self.backend.rows[self.plan.id]['state']=state
            for operation in (lambda:self.manager.save(replace(self.plan,name='next'),result['revision']),lambda:self.manager.remove(self.plan.id,result['revision'])):
                with self.assertRaises(RuntimeError):operation()
            self.assertEqual(self.store.path.read_bytes(),before)

    def test_stale_plan_or_modified_system_arguments_never_start(self):
        result=self.save(replace(self.plan,enabled=True))
        with self.assertRaises(ValueError):self.manager.run(self.plan.id,'old')
        self.backend.rows[self.plan.id]['xml']=self.backend.rows[self.plan.id]['xml'].replace('--scheduled-run','--program-update')
        with self.assertRaises(ValueError):self.manager.run(self.plan.id,result['revision'])
        self.assertNotIn('run',[call[0] for call in self.backend.calls])

    def test_remove_failure_keeps_disabled_plan_and_success_preserves_external_history(self):
        result=self.save(replace(self.plan,enabled=True));history=self.store.path.parent/'runs/report.json';history.parent.mkdir();history.write_text('history')
        self.backend.fail='remove'
        with self.assertRaises(OSError):self.manager.remove(self.plan.id,result['revision'])
        self.assertFalse(self.store.get(self.plan.id).enabled)
        self.backend.fail='';self.manager.remove(self.plan.id,revision(self.store.read()))
        self.assertEqual(self.store.read()['plans'],[]);self.assertEqual(history.read_text(),'history')

    def test_read_catalog_does_not_create_default_data_and_manual_run_returns_original_instance(self):
        self.assertFalse(self.store.path.exists());self.manager.catalog();self.assertFalse(self.store.path.exists())
        result=self.save(replace(self.plan,enabled=True))
        self.assertEqual(self.manager.run(self.plan.id,result['revision'])['instance'],'same-instance')

    def test_windows_export_can_omit_enabled_true_default_without_making_binding_stale(self):
        self.save(replace(self.plan,enabled=True));row=self.backend.rows[self.plan.id]
        tree=ET.fromstring(row['xml']);settings=tree.find('{'+NAMESPACE+'}Settings');settings.remove(settings.find('{'+NAMESPACE+'}Enabled'))
        row['xml']=ET.tostring(tree,encoding='unicode')
        self.assertTrue(self.manager.matches(replace(self.plan,enabled=True),row))
        row['enabled']=False;self.assertFalse(self.manager.matches(replace(self.plan,enabled=True),row))


if __name__=='__main__':unittest.main()
