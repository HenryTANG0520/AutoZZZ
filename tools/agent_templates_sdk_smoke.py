"""Actual SDK/source six-step crop/mask publication, archive restoration and conflict checks."""
from dataclasses import replace
import argparse
import base64
from io import BytesIO
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from autozzz.config import ROOT
from autozzz.onedragon_runtime import OneDragonClient,RuntimePaths,RequestPending,RemoteError


def main():
    import numpy as np
    from PIL import Image
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path);args=parser.parse_args()
    if args.output:args.output.mkdir(parents=True,exist_ok=True)
    checks=[]
    with tempfile.TemporaryDirectory(prefix='autozzz-agent-templates-') as temporary:
        root=Path(temporary);paths=replace(RuntimePaths.discover(ROOT,'cn'),data=root/'profile')
        image=np.zeros((1080,1920,3),dtype=np.uint8)
        image[:,:,0]=np.arange(1920,dtype=np.uint16)[None,:]%251
        image[:,:,1]=np.arange(1080,dtype=np.uint16)[:,None]%241
        image[:,:,2]=91
        screenshot=root/'游戏.png';Image.fromarray(image).save(screenshot)
        client=OneDragonClient(paths)
        def call(method,**params):
            request=client.submit(method,**params)
            while True:
                try:return client.result(request,timeout=.5)
                except RequestPending:continue
        try:
            try:client.start(timeout=.5)
            except RequestPending:
                while True:
                    try:client.finish_start(timeout=.5);break
                    except RequestPending:continue
            catalog=call('agent_template_catalog');assert len(catalog['steps'])==6
            assert any(row['template_id']=='anby' and row['agent_id']=='anby' for row in catalog['registered_templates'])
            draft=call('agent_template_open',agent='anby');token=draft['token']
            for i in range(6):
                draft=call('agent_template_import',token=token,index=i,path=str(screenshot))
                assert draft['images'][i] and draft['masks'][i] and draft['rect']
                left=draft['rect']
                crop_bytes=base64.b64decode(draft['images'][i]);mask_bytes=base64.b64decode(draft['masks'][i])
                decoded=np.array(Image.open(BytesIO(crop_bytes)))
                assert np.array_equal(decoded,image[left[1]:left[3],left[0]:left[2]])
                mask=np.array(Image.open(BytesIO(mask_bytes)));assert mask.shape==decoded.shape[:2] and mask.max()==255
                if args.output:
                    (args.output/(catalog['steps'][i]['key']+'-raw.png')).write_bytes(crop_bytes)
                    (args.output/(catalog['steps'][i]['key']+'-mask.png')).write_bytes(mask_bytes)
                if i==2:
                    other=call('agent_template_side',token=token,side='right')
                    assert other['rect'][0]>left[0] and other['rect'][2]>left[2]
                    assert other['images'][i]!=draft['images'][i]
                    right=other['rect'];decoded_right=np.array(Image.open(BytesIO(base64.b64decode(other['images'][i]))))
                    assert np.array_equal(decoded_right,image[right[1]:right[3],right[0]:right[2]])
                    if args.output:(args.output/'chain-right-raw.png').write_bytes(base64.b64decode(other['images'][i]))
                    call('agent_template_side',token=token,side='left')
                checks.append(catalog['steps'][i]['key'])
            saved=call('agent_template_save',token=token,agent='anby');assert saved['archive']
            reopened=call('agent_template_open',agent='anby')
            assert all(reopened['images']) and reopened['images']==draft['images'] and not reopened['changed']
            removed=call('agent_template_remove',agent='anby',signature=reopened['signature'])
            assert not any(row['id']=='anby' for row in removed['catalog']['agents'])
            restored=call('agent_template_restore',identifier=removed['archive'])
            assert any(row['id']=='anby' for row in restored['catalog']['agents'])
            reopened=call('agent_template_open',agent='anby')
            assert reopened['images']==draft['images'];checks.append('source_save_png_mask_delete_archive_restore')
            before=call('agent_template_preferences');values=before['values'];values['hotkeys']['battle_1']='Win+numpad_7'
            prefs=call('agent_template_preferences_save',values=values,revision=before['revision'])
            assert prefs['values']['hotkeys']['battle_1']=='Win+NUMPAD_7';checks.append('source_extended_hotkeys_presentation_config')
            assert reopened['registration']['registered'] and reopened['registration']['agent_id']=='anby'
            checks.append('registered_engine_agent_mapped')
            registered_before=call('agent_template_catalog')['registered_templates']
            custom=call('agent_template_open');custom_id='unregistered_new_character'
            for i in range(6):custom=call('agent_template_import',token=custom['token'],index=i,path=str(screenshot))
            saved=call('agent_template_save',token=custom['token'],agent=custom_id)
            assert not saved['draft']['registration']['registered'] and saved['draft']['registration']['agent_id'] is None
            assert saved['catalog']['registered_templates']==registered_before
            custom=call('agent_template_open',agent=custom_id);old_images=custom['images']
            custom=call('agent_template_import',token=custom['token'],index=2,path=str(screenshot),side='right')
            saved=call('agent_template_save',token=custom['token'],agent=custom_id)
            assert saved['draft']['images'][2]!=old_images[2] and not saved['draft']['registration']['registered']
            restored=call('agent_template_restore',identifier=saved['archive'])
            custom=call('agent_template_open',agent=custom_id);assert custom['images']==old_images
            removed=call('agent_template_remove',agent=custom_id,signature=custom['signature'])
            restored=call('agent_template_restore',identifier=removed['archive'])
            assert any(row['id']==custom_id and not row['registration']['registered'] for row in restored['catalog']['agents'])
            assert restored['catalog']['registered_templates']==registered_before
            checks.append('unknown_asset_create_save_edit_restore_does_not_register_engine_agent')
            execution=call('execution_status');assert not execution['ready'] and not execution['busy']
        finally:
            while True:
                try:client.close(timeout=.5);break
                except RequestPending:continue
        result=dict(actual_sdk=True,checks=checks,pixel_exact_rgb=True,mask_dimensions_verified=True,worker_exited=not client.alive,game_initialized=False,
            game_started=False,game_input_sent=False)
        if args.output:(args.output/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
