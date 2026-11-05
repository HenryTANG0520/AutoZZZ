"""Actual pinned SDK read tools over HTTP; fake observations, no game input."""
import argparse
import http.client
import json
from pathlib import Path
import shutil
import socket
import sys
from types import SimpleNamespace as NS
from unittest.mock import patch

root=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(root),str(root/'vendor/onedragon/src')]


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path)
    parser.add_argument('--refresh-catalog',action='store_true')
    parser.add_argument('--edits',action='store_true')
    args=parser.parse_args()
    from autozzz.onedragon_mcp_compat import generate_catalog
    catalog=generate_catalog(root/'vendor/onedragon')
    catalog_path=root/'autozzz/onedragon_mcp_catalog.json'
    if args.refresh_catalog:
        catalog_path.write_text(json.dumps(catalog,ensure_ascii=False,indent=2),encoding='utf-8')
        print('Catalog refreshed:',len(catalog['tools']),'tools,',len(catalog['prompts']),'prompts')
        if args.output is None:return
    assert catalog==json.loads(catalog_path.read_text(encoding='utf-8'))
    if args.output is None:raise ValueError('--output required for SDK smoke')
    output=args.output.resolve()
    if output.exists():raise ValueError('请选择新的证据目录')
    profile=output/'profile';(profile/'config').mkdir(parents=True)
    for path in (root/'vendor/onedragon/config').glob('*.yml'):
        shutil.copyfile(path,profile/'config'/path.name)
    import numpy as np
    from autozzz.onedragon_service import OneDragonService
    from zzz_od.backend import backend_context
    service=OneDragonService(root/'vendor/onedragon',profile)
    def forbidden(*_,**__):raise AssertionError('禁止物理输入或游戏启动')
    with patch('autozzz.windows.find_game',side_effect=forbidden),\
         patch('autozzz.windows.user32.SendInput',side_effect=forbidden):
        try:
            ctx=service.ctx
            ctx.ready_for_application=True
            frame=np.zeros((1080,1920,3),dtype=np.uint8)
            ctx.controller=NS(is_game_window_ready=True,
                game_win=NS(win_title='fixture-only',is_win_valid=True,is_win_active=True,is_win_scale=True,
                    win_rect=NS(x1=0,y1=0,width=1920,height=1080)),
                get_screenshot=lambda **_:frame.copy(),fill_uid_black=lambda image:image,
                close_game=forbidden,key_tap=forbidden,click=forbidden,drag_to=forbidden)
            ctx.ocr_service=NS(get_ocr_result_list=lambda **_:[NS(data='fixture text',x=10,y=10,w=50,h=20)])
            with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            manager=service.mcp_service;started=manager.start(port)
            session_id=None;sequence=0
            def rpc(method,params=None,notification=False):
                nonlocal sequence,session_id
                sequence+=1
                body=dict(jsonrpc='2.0',method=method,params=params or {})
                if not notification:body['id']=sequence
                headers={'Authorization':'Bearer '+started['bearer_token'],'Accept':'application/json, text/event-stream','Content-Type':'application/json'}
                if session_id:headers.update({'Mcp-Session-Id':session_id,'MCP-Protocol-Version':'2025-03-26'})
                connection=http.client.HTTPConnection('127.0.0.1',port,timeout=20)
                connection.request('POST','/mcp',json.dumps(body),headers)
                response=connection.getresponse();raw=response.read()
                if response.getheader('Mcp-Session-Id'):session_id=response.getheader('Mcp-Session-Id')
                assert response.status in (200,202),(response.status,raw)
                connection.close()
                value=json.loads(raw) if raw else None
                if notification:return
                assert 'result' in value,value
                return value['result']
            rpc('initialize',dict(protocolVersion='2025-03-26'))
            rpc('notifications/initialized',notification=True)
            assert len(rpc('tools/list')['tools'])==30
            assert len(rpc('prompts/list')['prompts'])==3
            results={}
            def call(name,arguments=None):
                response=rpc('tools/call',dict(name=name,arguments=arguments or {}))
                assert not response['isError'],(name,response)
                value=json.loads(response['content'][0]['text'])
                assert not (isinstance(value,dict) and (value.get('error') or value.get('ok')is False)),(name,value)
                results[name]=dict(ok=True,result_type=type(value).__name__)
                return value
            window=call('check_game_window');assert window['is_win_valid'] and window['win_title']=='fixture-only'
            capture=call('capture_game_screen');assert Path(capture).is_file()
            with patch.object(backend_context,'find_screen_matches',return_value=[]):
                screen=call('analyze_screen');assert screen['success'] and screen['ocr_texts'][0]['text']=='fixture text'
                screen=call('analyze_screen',dict(screenshot=capture));assert screen['success'] and screen['screenshot_path']is None
            call('list_applications');call('get_predefined_teams')
            operations=call('list_operations');assert operations['operations']
            op_id=operations['operations'][0]['op_id'];call('describe_operation',dict(op_id=op_id))
            configs=call('list_app_configs');assert configs['configs']
            app_id=configs['configs'][0]['app_id']
            call('get_config',dict(app_id=app_id));call('describe_config',dict(app_id=app_id))
            call('get_run_status');call('list_mcp_usage_guides');call('get_mcp_usage_guide',dict(name='zzz_check_status'))
            for name in ('zzz_check_status','zzz_run_one_dragon','zzz_run_standalone_app'):
                prompt=rpc('prompts/get',dict(name=name))
                assert prompt['messages'][0]['content']['text']
            if args.edits:edit_checks(manager,ctx,rpc,call,profile)
            assert service._execution is None  # Not even a second controlled execution was allocated.
            manager.stop()
            (output/'result.json').write_text(json.dumps(dict(success=True,tools=results,prompts=3,
                catalog_tool_count=28,extra_tools=2,game_input_sent=False,execution_created=False,
                editing_tested=args.edits,schema_source_hashes=catalog['source_hashes']),ensure_ascii=False,indent=2),encoding='utf-8')
            print('PASS:',len(results),'actual SDK tools + 3 original prompts over HTTP; no game input')
        finally:
            # Fixture owns no physical controller lifecycle.
            if service._ctx is not None:service._ctx.controller=None;service._ctx.ready_for_application=False
            service.close()


def edit_checks(manager,ctx,rpc,call,profile):
    from one_dragon.base.screen.screen_info import ScreenInfo
    ctx.screen_loader.save_screen(ScreenInfo(dict(screen_id='mcpfixture',screen_name='MCP fixture')))
    def rejected(name,arguments):
        response=rpc('tools/call',dict(name=name,arguments=arguments))
        assert response['isError'],response
    rejected('set_config',dict(app_id='charge_plan',key='loop',value=True))
    manager.edit_enable(['config','screens'])
    # Exact original tool schema; authorization/revision are local session state.
    before=call('get_config',dict(app_id='charge_plan'))
    changed=call('set_config',dict(app_id='charge_plan',key='loop',value=True))
    assert call('get_config',dict(app_id='charge_plan',key='loop'))['value'] is True
    manager.edit_restore('config',changed['archive'])
    assert call('get_config',dict(app_id='charge_plan'))['data']==before['data']
    added=call('add_config_item',dict(app_id='standalone_app',list_field='app_list',item_dict=dict(app_id='email')))
    call('delete_config_item',dict(app_id='standalone_app',list_field='app_list',item_id=added['id']))
    inserted=call('upsert_screen_area',dict(screen_name='MCP fixture',area_name='claim',pc_rect=[100,100,200,200],area_type='text',text='确认'))
    assert ctx.screen_loader.get_area('MCP fixture','claim').text=='确认'
    removed=call('delete_screen_area',dict(screen_name='MCP fixture',area_name='claim'))
    assert ctx.screen_loader.get_area('MCP fixture','claim') is None
    manager.edit_restore('screens',removed['archive'])
    assert ctx.screen_loader.get_area('MCP fixture','claim').text=='确认'
    # Restore conflict cannot overwrite a subsequent edit.
    call('upsert_screen_area',dict(screen_name='MCP fixture',area_name='claim',pc_rect=[101,101,200,200],area_type='text',text='领取'))
    try:manager.edit_restore('screens',removed['archive'])
    except ValueError:pass
    else:raise AssertionError('restore overwrote changed screen')
    # External changes close the session; no silent revision refresh.
    external=profile/'config/external.yml';external.write_text('external: true\n',encoding='utf-8')
    rejected('set_config',dict(app_id='charge_plan',key='loop',value=True))
    assert manager.editing.session is None
    manager.edit_enable(['config'])
    rejected('set_config',dict(app_id='charge_plan',key='run_times',value=99))
    assert manager.editing.session is None
    manager.edit_enable(['screens'])
    rejected('upsert_screen_area',dict(screen_name='MCP fixture',area_name='bad',pc_rect=[-1,0,2,3]))
    assert ctx.screen_loader.get_area('MCP fixture','bad') is None
    manager.edit_disable()


if __name__=='__main__':main()
