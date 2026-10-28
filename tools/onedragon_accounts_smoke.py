"""真实独立进程验证账号隔离、密码保留、配置切换与归档，不操作游戏。"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from autozzz.config import GAME_PATHS
from autozzz.onedragon_runtime import OneDragonClient,RuntimePaths,RemoteError


def main():
    defaults=RuntimePaths.discover(Path(__file__).resolve().parents[1])
    with tempfile.TemporaryDirectory(prefix="autozzz-accounts-") as folder:
        paths=RuntimePaths(defaults.source,defaults.python,Path(folder))
        client=OneDragonClient(paths)
        try:
            client.start()
            initial=client.call("account_list")
            assert initial["current"]==1 and len(initial["instances"])==1,initial
            created=client.call("account_action",action="create",revision=initial["revision"],name="账号隔离回放")
            assert created["instances"][-1]["idx"]==2 and not created["instances"][-1]["enabled"],created
            current=client.call("account_action",action="activate",revision=created["revision"],idx=2)
            assert current["current"]==2
            # 窗口仍引用旧账号时，初始化不得把游戏路径写入新账号。
            fixture=paths.data/'ZenlessZoneZero.exe'
            fixture.touch()
            before={p:p.read_bytes() for p in (paths.data/'config').rglob('*.yml')}
            started=client.call('initialize_execution',server='cn',game_path=str(fixture),instance_idx=1)
            deadline=time.monotonic()+5
            while True:
                state=client.call('execution_status')
                assert state['job_id']==started['job_id']
                if not state['busy']:break
                if time.monotonic()>deadline:raise AssertionError('account guard did not finish')
                time.sleep(.05)
            assert state['state']=='failed' and '账号配置已变化' in state['error'],state
            assert all(p.read_bytes()==raw for p,raw in before.items())
            account=client.call("account_describe",idx=2,server="cn")
            secret="TEST-PASSWORD-NOT-REAL"
            values=dict(game_path=GAME_PATHS["cn"],game_region="cn",game_language="cn",account="test-account-not-real",password=secret)
            saved=client.call("account_save",idx=2,server="cn",revision=account["revision"],values=values)
            assert saved["password_set"] and secret not in json.dumps(saved),saved.keys()
            account_file=paths.data/"config"/"02"/"game_account.yml"
            assert secret in account_file.read_text(encoding="utf-8")
            kept=client.call("account_save",idx=2,server="cn",revision=saved["revision"],values=dict(password="",account="changed-account"))
            assert kept["password_set"] and secret in account_file.read_text(encoding="utf-8")
            cleared=client.call("account_save",idx=2,server="cn",revision=kept["revision"],values=dict(clear_password=True))
            assert not cleared["password_set"] and secret not in account_file.read_text(encoding="utf-8")
            teams=client.call("describe_configuration",app_id="teams")
            rows=next(field["value"] for field in teams["fields"] if field["name"]=="team_list")
            rows[0]["name"]="仅账号2"
            client.call("save_configuration",app_id="teams",revision=teams["revision"],values=dict(team_list=rows))
            enabled=client.call("account_action",action="options",revision=current["revision"],idx=2,enabled=True,force_login=True)
            assert enabled["instances"][-1]["force_login"]
            again=client.call("account_action",action="activate",revision=enabled["revision"],idx=1)
            other=client.call("describe_configuration",app_id="teams")
            assert next(f["value"] for f in other["fields"] if f["name"]=="team_list")[0]["name"]!="仅账号2"
            rejected=0
            for method,params in (
                ("account_action",dict(action="rename",revision=initial["revision"],idx=2,name="stale")),
                ("account_action",dict(action="activate",revision=again["revision"],idx=999)),
                ("account_save",dict(idx=2,server="cn",revision=cleared["revision"],values=dict(game_region="eu"))),
                ("account_save",dict(idx=2,server="cn",revision=cleared["revision"],values=dict(password="new",clear_password=True))),
            ):
                before={p:p.read_bytes() for p in (paths.data/"config").rglob("*.yml")}
                try:
                    client.call(method,**params)
                    raise AssertionError("invalid or conflicting account request accepted")
                except RemoteError:rejected+=1
                assert all(p.read_bytes()==raw for p,raw in before.items())
            archived=client.call("account_action",action="archive",revision=again["revision"],idx=2)
            assert len(archived["instances"])==1 and not account_file.exists()
            assert (Path(archived["archive"])/"game_account.yml").is_file()
            assert (Path(archived["archive"])/"team.yml").is_file()
            second=OneDragonClient(paths)
            try:
                second.start()
                assert second.call("account_list")["current"]==1
                assert len(second.call("account_list")["instances"])==1
            finally:second.close()
            print(json.dumps(dict(accounts_created=1,isolated_teams=True,password_preserved_and_cleared=True,
                                  password_not_returned=True,archived_without_deletion=True,stale_account_cannot_initialize=True,rejected=rejected,game_started=False)))
        finally:client.close()


if __name__=="__main__":main()
