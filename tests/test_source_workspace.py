import json
from pathlib import Path
import subprocess
import tempfile
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from autozzz.source_workspace import SourceWorkspace,branch_name,REMOTE


def git(root,*args):
    result=subprocess.run(['git','-C',str(root),*args],capture_output=True,text=True,check=True)
    return result.stdout.strip()


class SourceWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.origin=self.root/'origin';self.origin.mkdir()
        git(self.origin,'init','--initial-branch=main');git(self.origin,'config','user.name','Fixture');git(self.origin,'config','user.email','fixture@example.invalid')
        (self.origin/'file.txt').write_text('one',encoding='utf-8');git(self.origin,'add','.');git(self.origin,'commit','-m','one')
        remote=str(self.origin)
        class FixtureWorkspace(SourceWorkspace):
            protocol='file'
        FixtureWorkspace.remote=remote
        self.manager=FixtureWorkspace(NS(data=self.root/'data',_assert_idle=lambda:None))
        self.addCleanup(self.manager.close)

    def run_job(self,action,branch=None):
        state=self.manager.start(action,branch)
        self.manager.worker.join(15)
        self.assertFalse(self.manager.busy)
        return self.manager.status()

    def test_create_select_branch_and_fast_forward_actual_git(self):
        self.assertEqual(self.run_job('create')['state'],'success')
        self.assertEqual(self.run_job('select','main')['state'],'success')
        self.assertEqual((self.manager.root/'file.txt').read_text(),'one')
        (self.origin/'file.txt').write_text('two');git(self.origin,'commit','-am','two')
        state=self.run_job('sync');self.assertEqual(state['state'],'success',state)
        self.assertEqual((self.manager.root/'file.txt').read_text(),'two')
        git(self.origin,'switch','-c','feature/demo');(self.origin/'extra.txt').write_text('feature')
        git(self.origin,'add','.');git(self.origin,'commit','-m','feature')
        listed=self.run_job('fetch');self.assertIn('feature/demo',listed['result']['branches'])
        state=self.run_job('select','feature/demo');self.assertEqual(state['state'],'success',state)
        self.assertEqual(state['result']['branch'],'feature/demo')
        self.assertTrue(state['result']['pinned_runtime_unchanged'])

    def test_dirty_and_untracked_changes_preserved(self):
        self.run_job('create');self.run_job('select','main')
        tracked=self.manager.root/'file.txt';tracked.write_text('my uncommitted change')
        untracked=self.manager.root/'notes.txt';untracked.write_text('my notes')
        state=self.run_job('sync');self.assertEqual(state['state'],'failed');self.assertIn('未提交',state['error'])
        self.assertEqual(tracked.read_text(),'my uncommitted change');self.assertEqual(untracked.read_text(),'my notes')
        status=self.manager.catalog();self.assertTrue(status['dirty']);self.assertIn('file.txt',status['diff'])

    def test_remote_or_extra_git_settings_rejected(self):
        self.run_job('create');git(self.manager.root,'remote','set-url','origin','https://example.invalid/repo.git')
        self.assertEqual(self.run_job('fetch')['state'],'failed')
        git(self.manager.root,'remote','set-url','origin',self.manager.remote)
        git(self.manager.root,'config','core.sshCommand','malicious')
        self.assertIn('额外 Git 设置',self.run_job('fetch')['error'])

    def test_nonempty_directory_not_overwritten(self):
        self.manager.root.mkdir(parents=True);file=self.manager.root/'keep.txt';file.write_text('keep')
        self.assertEqual(self.run_job('create')['state'],'failed');self.assertEqual(file.read_text(),'keep')

    def test_invalid_refs_never_reach_git(self):
        for name in ('--upload-pack=x','../main','x..y','x//y','main.lock','main/','$(calc)','a b','https://host/repo','x\ny'):
            with self.subTest(name=name),self.assertRaises(ValueError):branch_name(name)
        self.assertEqual(branch_name('feature/v2.1'),'feature/v2.1')
        self.assertEqual(SourceWorkspace.remote,REMOTE)

    def test_cancel_flag_stops_before_git_and_close_waits(self):
        self.manager.cancelled.set()
        with self.assertRaises(InterruptedError):self.manager._git('version')
        self.manager.close();self.assertIsNone(self.manager.process)

    def test_git_divergence_does_not_reset_local_history(self):
        self.run_job('create');self.run_job('select','main')
        git(self.manager.root,'config','user.name','local')
        git(self.manager.root,'config','user.email','local@example.invalid')
        # Extra settings are rejected; remove identity again after making local history.
        (self.manager.root/'local.txt').write_text('local');git(self.manager.root,'add','.');git(self.manager.root,'commit','-m','local')
        git(self.manager.root,'config','--unset','user.name');git(self.manager.root,'config','--unset','user.email')
        local=git(self.manager.root,'rev-parse','HEAD')
        (self.origin/'remote.txt').write_text('remote');git(self.origin,'add','.');git(self.origin,'commit','-m','remote')
        state=self.run_job('sync');self.assertEqual(state['state'],'failed')
        self.assertEqual(git(self.manager.root,'rev-parse','HEAD'),local)
        self.assertEqual((self.manager.root/'local.txt').read_text(),'local')

    def interrupt_after_init(self):
        original=self.manager._git
        def interrupt(*args,**kwargs):
            result=original(*args,**kwargs)
            if args and args[0]=='init':self.manager.cancelled.set()
            return result
        with patch.object(self.manager,'_git',side_effect=interrupt):state=self.run_job('create')
        self.assertEqual(state['state'],'cancelled',state)
        self.assertTrue((self.manager.root/'.git').is_dir())
        self.assertEqual(git(self.manager.root,'remote'),'')
        self.assertTrue(self.manager.initializing.is_file())

    def test_actual_git_init_interruption_recovers_with_new_manager_and_no_dirty_marker(self):
        self.interrupt_after_init()
        # A process restart must retain recovery capability; the marker is outside git status.
        self.manager=type(self.manager)(self.manager.service);self.addCleanup(self.manager.close)
        pending=self.manager.catalog();self.assertTrue(pending['initialization_pending'])
        state=self.run_job('create');self.assertEqual(state['state'],'success',state)
        self.assertFalse(self.manager.initializing.exists())
        self.assertEqual(git(self.manager.root,'remote','get-url','origin'),self.manager.remote)
        self.assertEqual(git(self.manager.root,'status','--porcelain=v1'),'')
        self.assertEqual(self.run_job('select','main')['state'],'success')

    def test_unknown_empty_git_repo_is_not_adopted_without_owned_marker(self):
        self.manager.root.mkdir(parents=True);git(self.manager.root,'init','--initial-branch=main')
        state=self.run_job('create');self.assertEqual(state['state'],'failed')
        self.assertEqual(git(self.manager.root,'remote'),'')
        self.assertFalse(self.manager.initializing.exists())

    def test_interrupted_init_preserves_added_files_and_lock(self):
        self.interrupt_after_init()
        note=self.manager.root/'notes.txt';note.write_text('keep this')
        state=self.run_job('create');self.assertEqual(state['state'],'failed')
        self.assertEqual(note.read_text(),'keep this');self.assertEqual(git(self.manager.root,'remote'),'')
        note.unlink()
        lock=self.manager.root/'.git/index.lock';lock.write_text('still owned')
        state=self.run_job('create');self.assertEqual(state['state'],'failed')
        self.assertEqual(lock.read_text(),'still owned');self.assertEqual(git(self.manager.root,'remote'),'')
        self.assertTrue(self.manager.initializing.exists())

    def test_interrupted_init_with_committed_history_is_not_adopted(self):
        self.interrupt_after_init()
        git(self.manager.root,'config','user.name','fixture');git(self.manager.root,'config','user.email','fixture@example.invalid')
        note=self.manager.root/'notes.txt';note.write_text('committed')
        git(self.manager.root,'add','.');git(self.manager.root,'commit','-m','manual history')
        commit=git(self.manager.root,'rev-parse','HEAD')
        state=self.run_job('create');self.assertEqual(state['state'],'failed')
        self.assertEqual(git(self.manager.root,'rev-parse','HEAD'),commit);self.assertEqual(git(self.manager.root,'remote'),'')

    def test_initialization_marker_cannot_recover_a_replaced_directory(self):
        self.interrupt_after_init()
        original=self.manager.root.with_name('original-preserved');self.manager.root.rename(original)
        self.manager.root.mkdir();git(self.manager.root,'init','--initial-branch=main')
        state=self.run_job('create');self.assertEqual(state['state'],'failed')
        self.assertIn('身份',state['error']);self.assertTrue((original/'.git').is_dir())
        self.assertEqual(git(self.manager.root,'remote'),'')

    def test_ignored_local_files_survive_branch_switch_and_fast_forward(self):
        (self.origin/'.gitignore').write_text('local.txt\n')
        git(self.origin,'add','.gitignore');git(self.origin,'commit','-m','ignore local data')
        self.run_job('create');self.run_job('select','main')
        note=self.manager.root/'local.txt';note.write_text('my ignored data')
        git(self.origin,'switch','-c','feature/track-local')
        (self.origin/'local.txt').write_text('upstream content')
        git(self.origin,'add','-f','local.txt');git(self.origin,'commit','-m','track formerly ignored file')
        head=git(self.manager.root,'rev-parse','HEAD')
        state=self.run_job('select','feature/track-local')
        self.assertEqual(state['state'],'failed',state)
        self.assertEqual(note.read_text(),'my ignored data')
        self.assertEqual(git(self.manager.root,'rev-parse','HEAD'),head)
        git(self.origin,'switch','main');git(self.origin,'merge','--ff-only','feature/track-local')
        state=self.run_job('sync')
        self.assertEqual(state['state'],'failed',state)
        self.assertEqual(note.read_text(),'my ignored data')
        self.assertEqual(git(self.manager.root,'rev-parse','HEAD'),head)
