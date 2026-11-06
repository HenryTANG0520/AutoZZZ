"""Actual candidate interpreter/source/bridge contract probe. Never initializes game input."""
import argparse
from contextlib import nullcontext
import json
from pathlib import Path
import sys

from .onedragon_bundle import filesystem_path, use_verified_site_packages


def probe(identity,checksum,data):
    from .runtime_selection import read_json,verify_identity
    metadata=read_json(identity);root=identity.parent
    verify_identity(identity,checksum,source=root/'source',python=Path(sys.executable),bridge=Path(__file__).resolve().parent.parent,
        revision=metadata['revision'],protocol=metadata['protocol'],require_compatible=False)
    use_verified_site_packages(root / 'env')
    runtime_root=filesystem_path(root)
    sys.path.insert(0,str(runtime_root/'source/src'))
    from one_dragon.utils import os_utils
    os_utils.set_work_dir(data)
    errors=[];checks=[];service=None;window=None;application=None;policies={};apps=[]
    def run(name,operation):
        try:
            value=operation();checks.append(name);return value
        except Exception as exc:errors.append(dict(check=name,error=type(exc).__name__+': '+str(exc)));return None
    try:
        from .onedragon_service import OneDragonService
        from .onedragon_runtime import EXECUTABLE_APPLICATIONS
        from .run_lock import profile_lock
        service=OneDragonService(root/'source',data,runtime_revision=metadata['revision'])
        with nullcontext():
            def context():
                from .onedragon_host import ManagedZContext
                def denied(*a,**kw):raise AssertionError('Compatibility probe must not initialize game controls')
                ManagedZContext.init_controller=denied
                with profile_lock(data):ctx=service._create_context()
                if ctx.controller is not None:raise AssertionError('Game controller unexpectedly initialized')
                return ctx
            ctx=run('actual_source_context_no_game',context)
            if ctx is None:raise RuntimeError('候选实际上下文未能构建。')
            catalog=run('application_factory_registration',service.catalog)
            if catalog is not None:
                apps=catalog['applications']
                if catalog['failures']or catalog['plugin_failures']:errors.append(dict(check='factory_failures',error=str(catalog['failures']+catalog['plugin_failures'])))
                builtins={row['id']for row in apps if not row['third_party']}
                missing=EXECUTABLE_APPLICATIONS-builtins;unknown=builtins-EXECUTABLE_APPLICATIONS
                if missing or unknown:errors.append(dict(check='complete_builtin_execution_adapter',error=dict(missing=sorted(missing),unknown=sorted(unknown))))
                for row in apps:
                    if row['configurable']:
                        result=run('configuration:'+row['id'],lambda key=row['id']:service.configuration(key))
                        if result and result['failures']:errors.append(dict(check='configuration:'+row['id'],error=result['failures']))
            for key in ('_game_input','_environment','_models','teams','battle_assistant','_group'):
                run('bridge_settings:'+key,lambda key=key:service.describe_configuration(key))
            for kind in ('auto_battle','dodge'):
                entries=run('policies:'+kind,lambda kind=kind:service.policies(kind))or []
                policies[kind]=len(entries)
                if not entries:errors.append(dict(check='policies:'+kind,error='No source policies available'))
                for row in entries:run('condition_engine:'+kind+':'+row['name'],lambda row=row,kind=kind:service.policy_validate(row['name'],kind=kind))
            def mcp():
                from .onedragon_mcp_compat import UpstreamCompatibility
                compat=UpstreamCompatibility(service);registry=compat.load()
                if len(registry.tools)!=len(compat.tools()):raise ValueError('Source MCP registrations differ from bridge catalog')
                return len(registry.tools)
            tools=run('actual_mcp_registrations_and_schemas',mcp)
            def native_pages():
                nonlocal window,application
                from PySide6.QtWidgets import QApplication
                from .onedragon_devtools_host import create_window
                class Lease:
                    def check(self):pass
                    def scope(self):raise AssertionError('No game capture during compatibility probe')
                application=QApplication.instance()or QApplication(['AutoZZZ SDK compatibility'])
                with profile_lock(data):
                    window=create_window(ctx,data,'cn',Lease(),lambda _:None)
                    if len(window.pages)!=3:raise ValueError('Incomplete native developer page set')
                    window.close();window.guard.__exit__(None,None,None);window=None;ctx.controller=None;application.processEvents()
                return True
            run('three_actual_native_developer_pages',native_pages)
            if ctx.controller is not None:errors.append(dict(check='input_disabled',error='Controller unexpectedly present'))
    except Exception as exc:errors.append(dict(check='probe_initialization',error=type(exc).__name__+': '+str(exc)))
    finally:
        if window is not None:
            window.close();window.guard.__exit__(None,None,None)
        if service is not None:
            if service._ctx is not None:service._ctx.controller=None
            service.close()
        if application is not None:application.processEvents()
    origins={name:str(Path(module.__file__).resolve())for name,module in list(sys.modules.items())if name in
        ('autozzz.onedragon_service','zzz_od.context.zzz_context','numpy','PySide6')and getattr(module,'__file__',None)}
    for name,path in origins.items():
        if not filesystem_path(Path(path)).is_relative_to(runtime_root):errors.append(dict(check='candidate_isolation',error=name+' loaded outside candidate: '+path))
    return dict(compatible=not errors,revision=metadata['revision'],checks=checks,errors=errors,policies=policies,
        applications=len(apps),third_party=[row['id']for row in apps if row['third_party']],origins=origins,
        python=sys.version.split()[0],game_started=False,game_initialized=False,game_input_sent=False,game_acceptance=False)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--runtime',type=Path,required=True);parser.add_argument('--digest',required=True);parser.add_argument('--data',type=Path,required=True)
    args=parser.parse_args();result=probe(args.runtime.resolve(),args.digest,args.data.resolve())
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
