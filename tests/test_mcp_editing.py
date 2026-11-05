import json
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from autozzz.config_transaction import atomic_apply, config_revision
from autozzz.onedragon_mcp_edit import ConfigArchives
from tests import test_onedragon_mcp as mcp_tests


class EditingTests(unittest.TestCase):
    setUp=mcp_tests.McpTests.setUp
    grant=mcp_tests.McpTests.grant
    http=mcp_tests.McpTests.http
    initialize=mcp_tests.McpTests.initialize

    def context(self):
        self.service.ctx=NS(current_instance_idx=1)
        self.manager.editing.refresh=lambda:None
        self.file=self.root/'config/01/app.yml';self.file.parent.mkdir(parents=True)
        self.file.write_bytes(b'old')

    def test_edit_permission_only_local_and_scoped(self):
        self.context()
        with self.assertRaisesRegex(ValueError,'本地MCP窗口'):
            self.manager.tool('set_config',dict(app_id='charge_plan',key='loop',value=True))
        self.manager.edit_enable(['config'])
        with self.assertRaisesRegex(ValueError,'本地MCP窗口'):
            self.manager.tool('delete_screen_area',dict(screen_name='x',area_name='y'))
        with self.assertRaises(ValueError):self.manager.tool('mcp_edit_enable',dict(scopes=['screens']))
        self.assertEqual(self.file.read_bytes(),b'old')
        self.assertFalse(self.service.calls)

    def test_success_updates_revision_revokes_grant_and_restores_archive(self):
        self.context();self.grant();self.manager.edit_enable(['config'])
        tx=ConfigArchives(self.root)
        def change(name,values):
            identifier=tx.publish({self.file:b'new'},dict(action='fixture'),lambda:True)
            return dict(ok=True,archive=identifier)
        old_revision=self.manager.editing.session['revision']
        with patch.object(self.manager.editing,'config_edit',side_effect=change):
            result=self.manager.tool('set_config',dict(app_id='charge_plan',key='loop',value=True))
        self.assertNotEqual(self.manager.editing.session['revision'],old_revision)
        self.assertIsNone(self.manager.grant)
        self.manager.edit_restore('config',result['archive'])
        self.assertEqual(self.file.read_bytes(),b'old')
        self.assertEqual(len(tx.list()),2)  # Restoring itself creates a reviewable archive.

    def test_external_revision_or_instance_change_revokes_edit_session(self):
        self.context();self.manager.edit_enable(['config'])
        self.file.write_bytes(b'external')
        with self.assertRaisesRegex(ValueError,'外部修改'):
            self.manager.tool('set_config',dict(app_id='charge_plan',key='loop',value=True))
        self.assertIsNone(self.manager.editing.session)
        self.manager.edit_enable(['config']);self.service.ctx.current_instance_idx=2
        with self.assertRaises(ValueError):self.manager.editing.check('config')
        self.assertIsNone(self.manager.editing.session)
        self.assertEqual(self.file.read_bytes(),b'external')

    def test_busy_task_cannot_edit_and_stop_revokes_mode(self):
        self.context();self.manager.edit_enable(['config']);self.service._execution.busy=True
        with patch.object(self.manager.editing,'config_edit') as write:
            with self.assertRaisesRegex(ValueError,'busy'):
                self.manager.tool('set_config',dict(app_id='charge_plan',key='loop',value=True))
            write.assert_not_called()
        self.service._execution.busy=False;self.manager.stop()
        self.assertIsNone(self.manager.editing.session)

    def test_archive_conflict_and_cross_account_restore_refused(self):
        self.context();tx=ConfigArchives(self.root)
        tx.publish({self.file:b'new'},dict(action='fixture'),lambda:True)
        identifier=tx.list()[0]['id'];self.file.write_bytes(b'external')
        self.manager.edit_enable(['config'])
        with self.assertRaises(ValueError):self.manager.edit_restore('config',identifier)
        self.assertEqual(self.file.read_bytes(),b'external')
        self.service.ctx.current_instance_idx=2;self.manager.edit_enable(['config'])
        with self.assertRaisesRegex(ValueError,'不属于当前账号'):
            self.manager.edit_restore('config',identifier)

    def test_failed_atomic_domain_method_preserves_bytes_and_archives(self):
        self.context();tx=ConfigArchives(self.root);path=self.file
        class Config:
            def __init__(self):self.file_path=str(path);self.data={'value':'old'}
            def _get_write_path(self):return self.file_path
            def save(self):Path(self._get_write_path()).write_text(json.dumps(self.data),encoding='utf-8')
        config=Config();before=path.read_bytes()
        def fail():
            config.data['value']='changed';config.save();raise ValueError('domain rejected')
        with self.assertRaisesRegex(ValueError,'domain rejected'):
            atomic_apply(config,config_revision(config),fail,self.root)
        self.assertEqual(path.read_bytes(),before);self.assertEqual(config.data,{'value':'old'})
        self.assertFalse(tx.list())
        self.assertFalse(tx.pending.exists())

    def test_pending_journal_prevents_reenable(self):
        self.context();tx=ConfigArchives(self.root)
        tx.pending.write_text('{}',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'保存'):self.manager.edit_enable(['config'])

    def test_interrupted_config_recovery_is_local_and_hash_bound(self):
        self.context();tx=ConfigArchives(self.root)
        identifier=tx.publish({self.file:b'new'},dict(action='fixture'),lambda:True)
        from autozzz.map_transaction import fingerprint
        raw=(tx.archive_dir(identifier)/'manifest.json').read_bytes()
        tx.pending.write_text(json.dumps(dict(id=identifier,manifest_hash=fingerprint(raw))),encoding='utf-8')
        choice=self.manager.editing.status()['archives']['config'][0]['id']
        self.assertTrue(choice.startswith('pending:'))
        with self.assertRaises(ValueError):self.manager.edit_restore('config','pending:changed')
        self.manager.edit_restore('config',choice)
        self.assertEqual(self.file.read_bytes(),b'old');self.assertFalse(tx.pending.exists())
        self.assertIsNone(self.manager.editing.session)


if __name__=='__main__':unittest.main()
