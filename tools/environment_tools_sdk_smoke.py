"""Real SDK EnvConfig atomic save and package-source use, with offline network fixtures."""
import argparse
import json
import shutil
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
from unittest.mock import patch

project=Path(__file__).resolve().parents[1];sys.path.insert(0,str(project))
sys.path.insert(0,str(project/'vendor/onedragon/src'))
from one_dragon.utils import os_utils
from one_dragon.envs.repo_config import RepoConfig
from autozzz.onedragon_environment import EnvironmentStore
from autozzz.environment_tools import EnvironmentTools,dependency_network,INDEXES


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='autozzz-env-sdk-') as temporary:
        root=Path(temporary);(root/'config').mkdir()
        shutil.copyfile(project/'vendor/onedragon/config/repository.yml',root/'config/repository.yml')
        os_utils.set_work_dir(root)
        repo=RepoConfig(prefer_bundled_config=True)
        service=NS(data=root,ctx=NS(repo_config=repo,push_service=NS(_refresh_secrets=lambda:None)),
            _assert_idle=lambda:None,_execution=None)
        store=EnvironmentStore(service);view=store.describe('_environment')
        field=next(row for row in view['fields'] if row['name']=='pip_source')
        assert {row['value'] for row in field['options']}==set(INDEXES)
        saved=store.save('_environment',dict(pip_source=INDEXES[1]),view['revision'])
        assert dependency_network()==(INDEXES[1],None)
        before=Path(store.config('_environment').file_path).read_bytes()
        for value,revision in (('https://unknown.invalid/simple',saved['revision']),(INDEXES[2],view['revision'])):
            try:store.save('_environment',dict(pip_source=value),revision);raise AssertionError('invalid save accepted')
            except ValueError:pass
            assert Path(store.config('_environment').file_path).read_bytes()==before
        tools=EnvironmentTools(service)
        try:
            with patch('autozzz.environment_tools.read_network',return_value=b'<a href="vgamepad-0.1.0.tar.gz">file</a>'):
                job=tools.start('pip_test',tools.catalog()['revision']);tools.worker.join(5)
            state=tools.status(job['job_id']);assert state['state']=='success'
            tools.apply(job['job_id'],INDEXES[2],job['revision'])
            assert dependency_network()==(INDEXES[2],None)
            tools.cancelled.clear()
            old=tools.catalog()['revision']
            with patch('autozzz.environment_tools.read_network',return_value=b'<a href="https://proxy.example.org">proxy</a>'):
                job=tools.start('proxy_discover',old);tools.worker.join(5)
            assert store.config('_environment').gh_proxy_url!='https://proxy.example.org'
            tools.apply(job['job_id'],'https://proxy.example.org',old)
            assert store.config('_environment').gh_proxy_url=='https://proxy.example.org'
            assert store.config('_environment').pip_source==INDEXES[2]
        finally:tools.close()
    result=dict(actual_sdk_config=True,pip_source_persisted=True,dependency_reads_selected_source=True,
        candidate_requires_apply=True,atomic_revision_preserved=True,unknown_source_rejected=True,
        worker_closed=True,network_fixture=True,game_input=False)
    (args.output/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result))


if __name__=='__main__':main()
