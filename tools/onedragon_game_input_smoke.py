"""账号键位配置的实际管道保存、来源映射、冲突与界面类型检查。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from autozzz.onedragon_runtime import OneDragonClient,RuntimePaths,RequestPending,RemoteError


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--data',type=Path,required=True);args=parser.parse_args()
    data=args.data.resolve()
    if data.exists():raise ValueError('请使用新的隔离验证目录。')
    defaults=RuntimePaths.discover(Path(__file__).resolve().parents[1]);paths=RuntimePaths(defaults.source,defaults.python,data)
    client=OneDragonClient(paths);checks=[]
    def rpc(method,**params):return client.call(method,timeout=30,**params)
    def describe():return rpc('describe_configuration',app_id='_game_input')
    def fields(description):return {item['name']:item for item in description['fields']}
    def save(values,description=None):return rpc('save_configuration',app_id='_game_input',revision=(description or describe())['revision'],values=values)
    try:
        client.start();initial=describe();f=fields(initial)
        assert len(f)==64 and f['key_normal_attack']['value']=='mouse_left'
        assert f['xbox_key_normal_attack']['value']=='xbox_x' and f['ds4_key_normal_attack']['value']=='ds4_square'
        assert f['xbox_action_compendium']['value']==['xbox_lt','xbox_a']
        assert not rpc('execution_status')['ready'];checks.append('actual_defaults_and_sixty_four_fields')
        updated=save(dict(key_normal_attack='j',key_interact='numpad_0',xbox_key_normal_attack='xbox_b',ds4_key_normal_attack='ds4_circle',
                          xbox_action_compendium=['xbox_lb','xbox_a'],ds4_action_map=[],xbox_key_press_time=.04,gamepad_turn_speed=1200,turn_dx=-6.5,type_input_way='input'))
        f=fields(updated);assert f['key_normal_attack']['value']=='j' and f['xbox_action_compendium']['value']==['xbox_lb','xbox_a']
        assert f['ds4_action_map']['value']==[];checks.append('all_device_types_and_combo_saved')
        original=(data/'config/01/game.yml').read_bytes()
        for invalid in (dict(key_interact='ctrl+a'),dict(key_dodge='mouse_invalid'),dict(xbox_key_normal_attack='ds4_cross'),
                        dict(ds4_key_normal_attack='xbox_a'),dict(xbox_action_map=['xbox_a','xbox_a']),dict(xbox_action_map=['ds4_cross']),
                        dict(gamepad_turn_speed=-1),dict(turn_dx=True),dict(ds4_key_press_time=0),dict(hdr=True)):
            try:save(invalid)
            except RemoteError:pass
            else:raise AssertionError(invalid)
            assert (data/'config/01/game.yml').read_bytes()==original
        try:save(dict(key_normal_attack='k'),initial)
        except RemoteError:pass
        else:raise AssertionError('stale revision accepted')
        checks.append('invalid_values_and_stale_revision_preserve_file')
        accounts=rpc('account_list');rpc('account_action',action='create',name='第二账号',revision=accounts['revision'])
        accounts=rpc('account_list');rpc('account_action',action='activate',idx=2,revision=accounts['revision'])
        assert fields(describe())['key_normal_attack']['value']=='mouse_left';save(dict(key_normal_attack='l'))
        accounts=rpc('account_list');rpc('account_action',action='activate',idx=1,revision=accounts['revision'])
        assert fields(describe())['key_normal_attack']['value']=='j';checks.append('per_account_isolation')
    finally:
        while True:
            try:client.close(timeout=1);break
            except RequestPending:continue
    client=OneDragonClient(paths)
    try:
        client.start();assert fields(describe())['xbox_key_normal_attack']['value']=='xbox_b';checks.append('fresh_worker_reads_saved_mappings')
    finally:
        while True:
            try:client.close(timeout=1);break
            except RequestPending:continue
    result=dict(checks=checks,actual_sdk_process=True,game_input_sent=False)
    (data/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
