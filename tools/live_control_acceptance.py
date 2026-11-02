"""Opt-in live acceptance using AutoZZZ's installed RuntimeSession and SDK.

Runs the already saved CN plan with the existing, unchanged test budget.
It pauses once through the same API as the run window, requests a real raw
capture while paused, resumes, and records terminal state. No custom game
input, account isolation, battery use, or alternative combat executor.
"""
from pathlib import Path
import argparse
import json
import queue
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'dist/AutoZZZ/onedragon/bridge'))
from autozzz.onedragon_runtime import RuntimePaths
from autozzz.onedragon_session import RuntimeSession
from autozzz.windows import is_admin


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live',action='store_true',help='Explicitly run the saved CN plan against the live game')
    parser.add_argument('--budget-id',required=True,help='Existing CN budget; this tool never grants or increases an allowance')
    parser.add_argument('--output',type=Path,required=True,help='New empty evidence directory')
    args=parser.parse_args()
    if not args.live:parser.error('Live game execution requires --live.')
    from autozzz.battle_budget import budget_directory,validate_runtime_budget
    import re
    if re.fullmatch(r'[a-zA-Z0-9_-]{1,80}',args.budget_id) is None:parser.error('Invalid budget identifier')
    ledger=json.loads((budget_directory()/(args.budget_id+'.json')).read_text(encoding='utf-8'))
    if ledger.get('server')!='cn' or ledger.get('id')!=args.budget_id:parser.error('Existing CN budget required')
    budget=dict(id=args.budget_id,allowance=ledger['allowance'],battle_limit=ledger['battle_limit'],max_per_battle=60)
    validate_runtime_budget(budget)
    out=args.output.resolve()
    if out.exists() and any(out.iterdir()):parser.error('Evidence directory must be new or empty')
    out.mkdir(parents=True,exist_ok=True)
    if not is_admin():
        (out/'result.json').write_text(json.dumps({'status':'not_started','reason':'Administrator token required for the existing game.'}),encoding='utf-8')
        return 2
    events=queue.Queue()
    session=RuntimeSession(RuntimePaths.discover(ROOT/'dist/AutoZZZ','cn'),events.put,report_root=out/'runtime')
    result={'status':'running','pause_requested':False,'pause_observed':False,'resume_observed':False,'captures':[],'states':[],'errors':[]}
    deadline=time.monotonic()+360
    started=False;resume_at=None;resume_sent=False;running_capture=False;terminal=None
    try:
        while time.monotonic()<deadline:
            if resume_at is not None and time.monotonic()>=resume_at and not resume_sent:
                session.pause(False);resume_sent=True
            try:event=events.get(timeout=.2)
            except queue.Empty:continue
            kind=event.get('type')
            if kind=='connected':
                session.send('rpc',method='describe_configuration',params={'app_id':'charge_plan'},tag='saved-plan')
            elif kind=='rpc_result' and event.get('tag')=='saved-plan':
                fields={f['name']:f['value'] for f in event['result']['fields']}
                rows=fields['plan_list']
                if (fields.get('loop') or len(rows)!=1 or rows[0]['category_name']!='区域巡防'
                        or rows[0]['plan_times']-rows[0]['run_times']!=1):
                    raise RuntimeError('Save exactly one remaining non-looped region patrol run before live acceptance')
                session.send('prepare',app_id='charge_plan',server='cn',region='cn',game_path=r'E:\miHoYo Launcher\games\ZenlessZoneZero Game\ZenlessZoneZero.exe')
            elif kind=='trace':
                trace=session.take_trace()
                snapshot=None if not trace else trace.get('snapshot')
                if snapshot and snapshot.get('capture'):
                    capture=snapshot['capture']
                    if not any(c['path']==capture['path'] for c in result['captures']):
                        result['captures'].append(capture)
                        index=len(result['captures'])
                        (out/f'capture-{index}-snapshot.json').write_text(json.dumps(snapshot,ensure_ascii=False,indent=2),encoding='utf-8')
            elif kind in ('trace_error','error'):
                result['errors'].append(event)
                if kind=='error' and not event.get('busy'):
                    raise RuntimeError(event.get('message','RuntimeSession error'))
            elif kind=='state':
                state=event['state'];control=state.get('control',{});battle=state.get('battle',{})
                result['states'].append(dict(state=state['state'],control=control,battle_running=battle.get('running'),budget=state.get('budget')))
                if state['state']=='prepared' and not started:
                    if state.get('prepared_requirements',{}).get('restore_charge') not in ('不使用','使用储蓄电量'):
                        raise RuntimeError('Battery restoration is not authorized')
                    resources=state.get('resources',{})
                    if any(a['id']!='charge_plan:restore' for a in resources.get('actions',[])):
                        raise RuntimeError('Prepared unexpected resource actions')
                    kwargs={}
                    if resources.get('confirmation_required'):kwargs['resource_confirmation']=resources['confirmation_id']
                    session.trace(True)
                    session.send('start',app_id='charge_plan',budget=budget,timeout_seconds=180,auto_launch=False,**kwargs)
                    started=True
                elif started and state.get('busy'):
                    if battle.get('running') and not result['pause_requested']:
                        session.pause(True);result['pause_requested']=True
                    elif result['pause_requested'] and control.get('manual_paused') and not result['pause_observed']:
                        result['pause_observed']=True
                        session.trace(True,capture=True)
                        resume_at=time.monotonic()+2
                    elif resume_sent and control.get('state')=='running' and battle.get('running'):
                        result['resume_observed']=True
                        if not running_capture:
                            session.trace(True,capture=True);running_capture=True
                elif started and not state.get('busy') and state['state'] in ('success','failed','stopped','interrupted'):
                    terminal=state;break
            elif kind=='closed':raise RuntimeError('Session closed before terminal state')
        if terminal is None:raise TimeoutError('Live acceptance observation deadline reached')
        result['execution']=terminal
        result['status']='passed' if (terminal['state']=='success' and result['pause_observed'] and result['resume_observed'] and result['captures']) else 'incomplete'
    except Exception as exc:
        result.update(status='failed',error=str(exc))
    finally:
        session.close()
        session.worker.join()
        result['worker_exited']=not session.worker.is_alive()
        result['report']=None if session.report_dir is None else str(session.report_dir/'report.json')
        (out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if result['status']=='passed' else 1


if __name__=='__main__':raise SystemExit(main())
