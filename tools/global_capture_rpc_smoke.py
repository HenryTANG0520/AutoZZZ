"""Real read-only SDK RPC with a tiny isolated config; no resource/model copying."""
import argparse
import json
from pathlib import Path
import shutil
import sys
import tempfile
from unittest.mock import patch

project=Path(__file__).resolve().parents[1];sys.path.insert(0,str(project))
from autozzz.onedragon_runtime import RuntimePaths,OneDragonClient,RequestPending,RemoteError


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    defaults=RuntimePaths.discover(project,use_selection=False)
    with tempfile.TemporaryDirectory(prefix='autozzz-capture-rpc-') as temporary:
        data=Path(temporary);(data/'config').mkdir();shutil.copyfile(defaults.source/'config/repository.yml',data/'config/repository.yml')
        paths=RuntimePaths(defaults.source,defaults.python,data,defaults.bundle,product_root=project,
            stable_data=data,generation='-',management_only=True)
        paths.verify()  # Keep fixed revision/runtime checks; only skip the large resource-copy phase below.
        client=OneDragonClient(paths)
        def call(method,**params):
            key=client.submit(method,**params)
            while True:
                try:return client.result(key,timeout=1)
                except RequestPending:continue
        try:
            with patch.object(RuntimePaths,'prepare',lambda _:None):
                try:hello=client.start()
                except RequestPending:
                    while True:
                        try:hello=client.finish_start(timeout=1);break
                        except RequestPending:continue
            assert hello['input_enabled'] is False
            state=call('global_capture_status');assert state['settings']['key_screenshot']=='f11' and not state['ready']
            try:call('global_capture_capture',token='0'*64);raise AssertionError('capture without target accepted')
            except RemoteError:pass
            assert call('execution_status')['ready'] is False
        finally:
            while True:
                try:client.close(timeout=1);break
                except RequestPending:continue
        assert not client.alive
    result=dict(actual_standalone_rpc=True,fixed_runtime_verified=True,management_connection=True,
        metadata_without_context=True,unbound_capture_refused=True,worker_exited=True,game_accessed=False,models_copied=False)
    (args.output/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result))


if __name__=='__main__':main()
