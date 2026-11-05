"""Real local-Git/uv/new-worker SDK activation and data-carrying rollback, without a game."""
import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from autozzz.config import ROOT
from autozzz.onedragon_runtime import RuntimePaths,UPSTREAM_REVISION,OneDragonClient,RequestPending
from autozzz.runtime_selection import bridge_fingerprint,resolve_selection,store_root,selection_path,read_json,digest
from autozzz.sdk_upgrade import SDKUpgradeManager
from autozzz.sdk_upgrade_files import unpack_archive,data_state,tree_hash


def git(root,*args):
    result=subprocess.run(['git','-C',str(root),*args],capture_output=True,text=True,encoding='utf-8',errors='replace',
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),check=True)
    return result.stdout.strip()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--fixture',type=Path,required=True);parser.add_argument('--online',action='store_true')
    parser.add_argument('--groups',default='dev');parser.add_argument('--keep-candidate',action='store_true');args=parser.parse_args()
    fixture=args.fixture.resolve();bridge=fixture/'frozen-bridge'
    if not fixture.name.startswith('autozzz-sdk-upgrade-acceptance-')or not bridge.is_dir():raise ValueError('Use the dedicated fixture with a frozen bridge snapshot.')
    from autozzz.battle_budget import budget_directory
    budget=budget_directory();budget_before={p.name:digest(p)for p in budget.glob('*')if p.is_file()}if budget.exists()else{}
    localappdata_before=os.environ.get('LOCALAPPDATA')
    data=fixture/'profiles/cn';baseline=RuntimePaths.discover(ROOT,'cn',use_selection=False)
    paths=replace(baseline,data=data,stable_data=data,bridge=bridge)
    paths.prepare();before=data_state(data)
    service=SimpleNamespace(data=data,stable_data=data,product_root=ROOT,_assert_idle=lambda:None,_environment_changed=False)
    manager=SDKUpgradeManager(service,paths,'cn');workspace=manager.workspace
    groups=[g.strip()for g in args.groups.split(',')if g.strip()];checks=[];candidate_id=None
    def wait():
        while manager.busy:
            state=manager.status()
            print(json.dumps(dict(event='progress',job=state['job_id'],phase=state['phase']),ensure_ascii=False),flush=True)
            manager.worker.join(timeout=3)
        state=manager.status()
        print(json.dumps(dict(event='job_result',result=state),ensure_ascii=False),flush=True)
        if state['state']!='success':raise RuntimeError(state['error'])
        return state['result']
    try:
        if not (workspace.root/'.git').exists():
            workspace._operation('create',None)
            archive=fixture/'fixed-source.tar'
            git(baseline.source,'archive','--format=tar','--output='+str(archive),UPSTREAM_REVISION)
            seed=fixture/'git-seed';unpack_archive(archive,seed,threading.Event());shutil.copytree(seed,workspace.root,dirs_exist_ok=True)
            git(workspace.root,'config','user.name','AutoZZZ offline SDK fixture');git(workspace.root,'config','user.email','autozzz-fixture@localhost')
            # A genuine new commit, containing the exact fixed source and a harmless provenance note.
            (workspace.root/'AUT OZZZ_FIXTURE'.replace(' ','' )).write_text('Offline compatibility fixture from '+UPSTREAM_REVISION+'\n',encoding='utf-8')
            git(workspace.root,'add','.');git(workspace.root,'commit','-m','offline fixture of fixed upstream for actual SDK upgrade')
        revision=git(workspace.root,'rev-parse','HEAD');assert revision!=UPSTREAM_REVISION
        review=manager.review(groups)
        print(json.dumps(dict(event='review',review={k:v for k,v in review.items()if k not in ('data_state',)}),ensure_ascii=False),flush=True)
        manager.build(review['id'],review['review_digest'],True,offline=not args.online);candidate=wait();candidate_id=candidate['id']
        assert candidate['hello']['revision']==revision;checks.append('actual_git_archive_and_frozen_uv_sync_full_compatibility_new_worker_hello')
        assert data_state(data)==before;checks.append('original_profile_bytes_unchanged_by_candidate_build')
        manager.activate(candidate_id,manager.catalog()['selection_revision'],True);activated=wait()
        assert activated['revision']==revision and activated['hello']['revision']==revision
        selected=resolve_selection(data,stable_source=paths.source,stable_python=paths.python,stable_bridge=bridge,stable_bundle=paths.bundle,stable_revision=UPSTREAM_REVISION,protocol=1)
        assert selected.revision==revision;checks.append('atomic_activation_and_post_switch_actual_revision_hello')
        # Model an already-completed account operation; rollback must carry this forward, never restore stale profile bytes.
        carried=selected.data/'config/sdk-carry-forward-fixture.yml';carried.write_text('completed_operation: true\n',encoding='utf-8')
        user_result=carried.read_bytes()
        manager.rollback(manager.catalog()['selection_revision'],True);rolled=wait()
        assert rolled['revision']==UPSTREAM_REVISION and (Path(rolled['data'])/carried.relative_to(selected.data)).read_bytes()==user_result
        assert data_state(data)==before;checks.append('stable_sdk_rollback_carries_current_operation_record_and_preserves_original_profile')
        budget_after={p.name:digest(p)for p in budget.glob('*')if p.is_file()}if budget.exists()else{}
        assert budget_after==budget_before and os.environ.get('LOCALAPPDATA')==localappdata_before
        checks.append('actual_global_budget_file_hashes_and_localappdata_preserved')
        root=store_root(data)/'candidates'/candidate_id
        print(json.dumps(dict(event='success',checks=checks,source_revision=revision,stable_revision=UPSTREAM_REVISION,
            frozen_bridge=str(bridge),bridge_hash=bridge_fingerprint(bridge),candidate=str(root),candidate_probe=read_json(root/'probe.json'),
            active_runtime=rolled,original_profile_unchanged=True,localappdata_unchanged=True,global_budget_directory_unchanged=True,
            game_started=False,game_input_sent=False,driver_installed=False,uv_online=args.online),ensure_ascii=False),flush=True)
        if not args.keep_candidate:
            expected=(store_root(data)/'candidates').resolve()
            if root.resolve().parent!=expected or read_json(root/'candidate.json')['stable_data']!=str(data):raise ValueError('Refusing to clean an unowned candidate.')
            shutil.rmtree(root)
            print(json.dumps(dict(event='owned_test_candidate_cleaned',candidate_id=candidate_id)),flush=True)
    finally:manager.close()


if __name__=='__main__':main()
