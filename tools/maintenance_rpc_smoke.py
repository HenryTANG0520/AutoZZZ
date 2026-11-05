"""Exercise maintenance RPCs in the real standalone worker; no context/game init."""
import argparse
import json
from pathlib import Path
import socket
import sys

project=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(project))
from autozzz.onedragon_runtime import RuntimePaths,OneDragonClient,RequestPending


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--package',type=Path)
    args=parser.parse_args()
    directory=args.output.resolve()
    if directory.exists():raise ValueError('请使用新的验证目录')
    directory.mkdir(parents=True)
    defaults=RuntimePaths.discover(args.package.resolve() if args.package else project)
    paths=RuntimePaths(defaults.source,defaults.python,directory/'profile',defaults.bundle)
    client=OneDragonClient(paths)
    def call(method,**params):
        request=client.submit(method,**params)
        while True:
            try:return client.result(request,timeout=1)
            except RequestPending:continue
    try:
        try:hello=client.start()
        except RequestPending:
            while True:
                try:hello=client.finish_start(timeout=1);break
                except RequestPending:continue
        assert hello['input_enabled'] is False
        fixture=paths.data/'config/auto_battle/autozzz-archive-smoke.yml'
        fixture.parent.mkdir(parents=True,exist_ok=True)
        original=b'author: AutoZZZ smoke\r\nscenes: []\r\n'
        fixture.write_bytes(original)
        policy=call('policy_read',name='autozzz-archive-smoke',merged=False)
        archived=call('policy_remove',name=policy['name'],revision=policy['revision'])
        assert not fixture.exists()
        restored=call('policy_restore',archive_id=archived['archive_id'])
        assert fixture.read_bytes()==original and restored['revision']==policy['revision']
        gamepad=call('gamepad_catalog')
        assert gamepad['driver']['game_verified'] is False
        source=call('source_catalog')
        assert source['exists'] is False and source['pinned_runtime_unchanged'] is True
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        started=call('mcp_start',port=port)
        assert started['running'] and started['bearer_token']
        assert call('mcp_status')['running']
        stopped=call('mcp_stop')
        assert not stopped['running']
        assert call('execution_status')['ready'] is False
        result=dict(standalone_python=hello['python'],revision=hello['revision'],
            gamepad_catalog=True,source_catalog=True,mcp_lifecycle=True,custom_policy_archive=True,
            context_initialized=False,game_input=False,driver_installed=False,source_downloaded=False)
    finally:
        while True:
            try:client.close(timeout=1);break
            except RequestPending:continue
    result['worker_exited']=not client.alive
    (directory/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result))


if __name__=='__main__':main()
