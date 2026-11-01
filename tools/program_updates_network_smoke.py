"""Actual release metadata through the installed SDK; no download, install or game."""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from autozzz.onedragon_runtime import OneDragonClient,RuntimePaths,RequestPending


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--package',type=Path,required=True)
    parser.add_argument('--data',type=Path,required=True);parser.add_argument('--proxy')
    args=parser.parse_args();base=args.data.resolve();project=Path(__file__).resolve().parents[1]
    if base.exists() or not base.is_relative_to(project/'runs'):raise ValueError('Use a new evidence directory under runs.')
    base.mkdir(parents=True);client=OneDragonClient(replace(RuntimePaths.discover(args.package.resolve()),data=base/'profile'))
    pending_job=None;result={}
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
        if args.proxy:
            config=call('describe_configuration',app_id='_environment')
            call('save_configuration',app_id='_environment',revision=config['revision'],
                 values=dict(proxy_type='personal',personal_proxy=args.proxy))
        pending_job=call('program_check',channel='stable',page=1)['job_id'];deadline=time.monotonic()+120
        while True:
            status=call('program_status',job_id=pending_job)
            assert status['job_id']==pending_job
            if not status['busy']:break
            if time.monotonic()>deadline:raise TimeoutError('Original release metadata request is still pending; no second job started.')
            time.sleep(.1)
        assert status['state']=='success',status
        execution=call('execution_status');assert not execution.get('ready') and not execution.get('busy')
        catalog=status['result']
        result=dict(actual_network=True,actual_installed_sdk=True,state=status['state'],channel=catalog['channel'],page=catalog['page'],
            releases=len(catalog['releases']),has_more=catalog['has_more'],unavailable_reason=catalog['unavailable_reason'],
            download_started=False,installation_started=False,game_initialized=False,game_input_sent=False)
    finally:
        if pending_job:
            status=call('program_status',job_id=pending_job)
            if status['busy']:call('program_cancel',job_id=pending_job)
        while True:
            try:client.close(timeout=.5);break
            except RequestPending:continue
        result['worker_exited']=not client.alive
        (base/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
