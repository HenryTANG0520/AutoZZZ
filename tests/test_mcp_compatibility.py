import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest

from autozzz.onedragon_mcp_compat import UpstreamCompatibility, READ_TOOLS, source_hashes
from tests import test_onedragon_mcp as mcp_tests


class CompatibilityTests(unittest.TestCase):
    setUp=mcp_tests.McpTests.setUp
    grant=mcp_tests.McpTests.grant
    http=mcp_tests.McpTests.http
    initialize=mcp_tests.McpTests.initialize

    def test_catalog_has_all_original_names_types_defaults(self):
        tools={row['name']:row for row in self.manager.tool_catalog()}
        self.assertEqual(len(tools),30)
        schema=tools['analyze_screen']['inputSchema']
        self.assertNotIn('required',schema)
        self.assertIsNone(schema['properties']['screenshot']['default'])
        self.assertEqual(schema['properties']['screenshot']['anyOf'],[{'type':'string'},{'type':'null'}])
        self.assertEqual(tools['get_config']['inputSchema']['required'],['app_id'])
        self.assertTrue(tools['get_config']['annotations']['readOnlyHint'])
        self.assertEqual(tools['key_tap']['inputSchema']['properties']['press_time']['default'],0.)

    def test_guides_and_three_original_prompts_work_without_sdk(self):
        self.initialize()
        status,body,_=self.http(dict(jsonrpc='2.0',id=2,method='prompts/list'),headers=self.session)
        self.assertEqual(status,200)
        self.assertEqual([p['name'] for p in body['result']['prompts']],
            ['zzz_check_status','zzz_run_one_dragon','zzz_run_standalone_app'])
        request=dict(jsonrpc='2.0',id=3,method='prompts/get',params=dict(name='zzz_run_standalone_app',arguments=dict(app_id='email')))
        _,body,_=self.http(request,headers=self.session)
        text=body['result']['messages'][0]['content']['text']
        self.assertIn("app_id='email'",text)
        guide=self.manager.tool('get_mcp_usage_guide',dict(name='zzz_run_standalone_app',app_id='email'))
        self.assertEqual(text,guide)
        self.assertEqual(len(self.manager.tool('list_mcp_usage_guides',{})),4)
        self.assertFalse(self.service.calls)

    def test_prompt_errors_and_bad_typed_arguments_are_reported(self):
        self.initialize()
        _,body,_=self.http(dict(jsonrpc='2.0',id=2,method='prompts/get',params=dict(name='missing')),headers=self.session)
        self.assertEqual(body['error']['code'],-32602)
        for args in [dict(name=1),dict(name='zzz_check_status',extra=True)]:
            with self.assertRaises(ValueError):self.manager.tool('get_mcp_usage_guide',args)
        with self.assertRaises(ValueError):self.manager.compatibility.prompt('zzz_run_standalone_app',dict(app_id=1))

    def test_upstream_run_aliases_share_single_grant_and_same_budget(self):
        with self.assertRaisesRegex(ValueError,'单次执行授权'):
            self.manager.tool('run_standalone_app',dict(app_id='charge_plan'))
        self.grant()
        with self.assertRaisesRegex(ValueError,'不一致'):
            self.manager.tool('run_standalone_app',dict(app_id='email'))
        self.assertFalse(self.manager.grant['consumed'])
        result=self.manager.tool('run_standalone_app',dict(app_id='charge_plan',block=False))
        self.assertTrue(result['started']);self.assertEqual(result['job_id'],'run-1')
        self.assertEqual(self.service.calls[0][1]['budget'],self.budget)
        self.assertEqual(self.manager.run_description()['prepared_job'],'prepared-1')
        with self.assertRaises(ValueError):self.manager.tool('run_standalone_app',{})
        self.assertEqual(len(self.service.calls),1)
        self.assertEqual(self.path.read_bytes(),self.original)

    def test_all_write_and_input_names_refuse_without_side_effects(self):
        self.grant()
        examples={'click_game':dict(x=10,y=10),'key_tap':dict(key='w'),
            'drag':dict(x1=0,y1=0,x2=1,y2=1),'input_text':dict(text='test'),
            'close_game':{},'upsert_screen_area':dict(screen_name='s',area_name='a',pc_rect=[0,0,1,1]),
            'delete_screen_area':dict(screen_name='s',area_name='a'),
            'set_config':dict(app_id='coffee',key='x',value=True),
            'add_config_item':dict(app_id='charge_plan',list_field='plan_list',item_dict={}),
            'delete_config_item':dict(app_id='charge_plan',list_field='plan_list',item_id='1'),
            'run_operation':dict(op_id='zzz_od.operation.test.Test'),'open_game':dict(block=False)}
        for name,args in examples.items():
            with self.subTest(name=name),self.assertRaises(ValueError):self.manager.tool(name,args)
        self.assertFalse(self.service.calls)
        self.assertFalse(self.manager.grant['consumed'])

    def test_blocking_alias_rejected_before_grant_consumption(self):
        self.grant()
        with self.assertRaisesRegex(ValueError,'block=false'):
            self.manager.tool('run_standalone_app',dict(block=True))
        self.assertFalse(self.manager.grant['consumed'])
        self.assertFalse(self.service.calls)

    def test_changed_fixed_source_fails_closed(self):
        source=Path(__file__).resolve().parents[1]/'vendor/onedragon'
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);directory=root/'src/zzz_od/backend/mcp';directory.mkdir(parents=True)
            for name in source_hashes(source):
                (directory/name).write_bytes((source/'src/zzz_od/backend/mcp'/name).read_bytes())
            (directory/'app.py').write_text('# changed',encoding='utf-8')
            with self.assertRaisesRegex(RuntimeError,'源码已变化'):UpstreamCompatibility(NS(source=root))

    def test_read_backend_never_constructs_second_run_slot(self):
        # Catalog and guide discovery never touch ctx, controller or executor.
        self.assertIsNone(self.manager.compatibility.registry)
        self.manager.tool('get_mcp_usage_guide',dict(name='zzz_check_status'))
        self.assertIsNone(self.manager.compatibility.registry)
        self.assertFalse(self.service.calls)


if __name__=='__main__':unittest.main()
