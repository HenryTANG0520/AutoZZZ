"""两类策略的实际 SDK 管道读写、校验和默认值隔离；不初始化游戏。"""
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
    defaults=RuntimePaths.discover(Path(__file__).resolve().parents[1]);client=OneDragonClient(RuntimePaths(defaults.source,defaults.python,data))
    checks=[]
    def rpc(method,**params):return client.call(method,timeout=30,**params)
    def rejected(method,**params):
        try:rpc(method,**params)
        except RemoteError:return
        raise AssertionError('未拒绝：'+method)
    try:
        client.start();initial=rpc('configuration',app_id='battle_assistant')['fields']
        for kind,count in (('auto_battle',8),('dodge',7)):
            policies=rpc('policies',kind=kind);assert len(policies)==count
            for policy in policies:
                assert rpc('policy_read',name=policy['name'],kind=kind)['content']
                assert rpc('policy_validate',name=policy['name'],kind=kind)['scenes']
            checks.append(kind+'_all_builtin_read_and_validate')
        name='同名自定义策略'
        contents={kind:f'author: {kind}\nscenes:\n  - handlers:\n      - states: ""\n        operations:\n          - op_name: 等待秒数\n            seconds: 0.01\n' for kind in ('auto_battle','dodge')}
        for kind,content in contents.items():
            before={p.name for p in (data/'config'/kind).glob('*.yml')}
            assert rpc('policy_check',content=content,kind=kind)['scenes']
            assert {p.name for p in (data/'config'/kind).glob('*.yml')}==before
            rpc('policy_save',name=name,content=content,kind=kind)
            assert rpc('policy_read',name=name,kind=kind)['content']==content
            rejected('policy_save',name=name,content='scenes: [broken]',kind=kind)
            assert rpc('policy_read',name=name,kind=kind)['content']==content
        checks.append('same_name_files_are_separate_and_bad_save_preserves_content')
        rpc('set_battle_options',values=dict(dodge_assistant_config=name))
        fields=rpc('configuration',app_id='battle_assistant')['fields']
        assert fields['auto_battle_config']==initial['auto_battle_config'] and fields['dodge_assistant_config']==name
        rpc('set_battle_options',values=dict(auto_battle_config=name))
        fields=rpc('configuration',app_id='battle_assistant')['fields'];assert fields['auto_battle_config']==fields['dodge_assistant_config']==name
        checks.append('defaults_are_independent')
        rejected('policy_save',name='闪避',content=contents['dodge'],kind='dodge')
        rejected('policy_read',name='全配队通用',kind='dodge')
        for kind in ('../auto_battle','auto_battle/../dodge',None,False):
            for method,params in (('policies',{}),('policy_read',dict(name=name)),('policy_validate',dict(name=name)),
                                  ('policy_check',dict(content=contents['dodge'])),('policy_save',dict(name=name,content=contents['dodge']))):
                rejected(method,kind=kind,**params)
        checks.append('builtin_wrong_kind_and_path_inputs_rejected')
        assert not list((data/'config').rglob('autozzz-check-*.yml'))
        assert not rpc('execution_status')['ready']
    finally:
        while True:
            try:client.close(timeout=1);break
            except RequestPending:continue
    result=dict(checks=checks,actual_sdk_process=True,models_initialized=False,game_input_sent=False)
    (data/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
