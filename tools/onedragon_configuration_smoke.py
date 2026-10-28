"""验证实际一条龙配置的类型、领域保存、冲突保护与配置隔离，不访问游戏。"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from autozzz.onedragon_runtime import OneDragonClient, RemoteError, RuntimePaths
from autozzz.run_lock import profile_lock


def main():
    defaults = RuntimePaths.discover(Path(__file__).resolve().parents[1])
    with tempfile.TemporaryDirectory(prefix="autozzz-config-") as folder:
        paths = RuntimePaths(defaults.source, defaults.python, Path(folder))
        logs = []
        client = OneDragonClient(paths, log=logs.append)
        try:
            hello = client.start()
            catalog = client.call("catalog", timeout=60)
            options = client.call("configuration_options")
            apps = [a["id"] for a in catalog["applications"] if a["configurable"]]
            apps += ["teams", "_group", "battle_assistant"]+[c["id"] for c in options["challenges"]]
            descriptions = {key: client.call("describe_configuration", app_id=key) for key in apps}
            assert all(d["fields"] for d in descriptions.values())
            assert "异常演练" in [m["value"] for m in options["training"]["实战模拟室"]["types"]["代理人晋升"]["missions"]]
            def save(app, values, description=None):
                description = description or client.call("describe_configuration", app_id=app)
                return client.call("save_configuration", app_id=app, values=values, revision=description["revision"])
            def values(description):
                return {f["name"]: f["value"] for f in description["fields"]}
            plan = dict(category_name="实战模拟室", mission_type_name="代理人晋升", mission_name="异常演练",
                        plan_times=2, run_times=0, card_num="1", predefined_team_idx=0)
            result = save("charge_plan", {"plan_list": [plan], "loop": False, "restore_charge": "不使用"})
            saved_plan = values(result)["plan_list"][0]
            assert saved_plan["mission_name"] == "异常演练" and saved_plan["plan_id"]
            saved_plan["plan_times"] = 3
            assert values(save("charge_plan", {"plan_list": [saved_plan]}))["plan_list"][0]["plan_times"] == 3
            save("charge_plan", {"combat_simulation_double_reward_config": plan})
            teams = values(descriptions["teams"])["team_list"]
            teams[0].update(name="坏 女人", agent_id_list=[options["agents"][1]["value"],"unknown","unknown"])
            assert values(save("teams", {"team_list": teams}))["team_list"][0]["name"] == "坏女人"
            defense = [dict(team_idx=0,weakness_list=["ETHER","FIRE"],for_critical=True)]
            assert values(save("shiyu_defense", {"team_list":defense}))["team_list"] == defense
            codes = [dict(code="TEST-NOT-REDEEMED",expires=20261231)]
            assert values(save("redemption_code", {"codes":codes}))["codes"] == codes
            group = values(descriptions["_group"])["app_list"][::-1]
            group[0]["enabled"] = True
            assert values(save("_group", {"app_list":group}))["app_list"] == group
            save("screenshot_helper", {"frequency_second":.7})
            save("commission_assistant", {"dialog_click_interval":.8})
            a = values(client.call("describe_configuration", app_id="screenshot_helper"))
            b = values(client.call("describe_configuration", app_id="commission_assistant"))
            assert a["frequency_second"] == .7 and b["dialog_click_interval"] == .8
            directory = paths.data/"config"/"01"/"one_dragon"
            assert (directory/"screenshot_helper.yml").is_file() and (directory/"commission_assistant.yml").is_file()
            challenge = next(c['id'] for c in options['challenges'] if c['id'].startswith('lost_void_challenge:'))
            folder,name = challenge.split(':',1)
            template = paths.data/'config'/folder/(name+'.sample.yml')
            template_raw = template.read_bytes()
            assert values(save(challenge,{'store_blood_min':51}))['store_blood_min']==51
            assert template.read_bytes()==template_raw and template.with_name(name+'.yml').is_file()
            pawn = descriptions['suibian_temple']
            priorities = next(f['value'] for f in pawn['fields'] if f['name']=='pawnshop_crest_priority')[::-1]
            assert values(save('suibian_temple',{'pawnshop_crest_priority':priorities}))['pawnshop_crest_priority']==priorities
            stale = client.call("describe_configuration", app_id="coffee")
            save("coffee", {"run_charge_plan_afterwards": True}, stale)
            rejected = 0
            for app, changes, description in (
                ("coffee", {"run_charge_plan_afterwards":False}, stale),
                ("coffee", {"run_charge_plan_afterwards":"true"}, None),
                ("charge_plan", {"plan_list":[dict(plan, mission_name="不存在")]},None),
                ("charge_plan", {"plan_list":[dict(plan, plan_times=-1)]},None),
                ("charge_plan", {"last_daily_reset_dt":"20260101"},None),
                ("notorious_hunt", {"weekly_challenge_start_weekday":8},None),
                ("teams", {"team_list":[dict(teams[0], auto_battle="不存在")]},None),
                ("suibian_temple", {"pawnshop_crest_priority":["不存在的物品"]},None),
                ("redemption_code", {"codes":[dict(code='INVALID-DATE',expires=2026101)]},None),
            ):
                before = {p:p.read_bytes() for p in (paths.data/"config").rglob("*.yml")}
                try:
                    save(app,changes,description)
                    raise AssertionError(f"must reject {app} {changes}")
                except RemoteError: rejected+=1
                assert all(p.read_bytes()==raw for p,raw in before.items()), app
            with profile_lock(paths.data):
                try:
                    client.call("describe_configuration",app_id="coffee")
                    raise AssertionError("profile lock must reject concurrent editor")
                except RemoteError: rejected+=1
            cold = OneDragonClient(paths)
            try:
                cold.start()
                before = {p:p.read_bytes() for p in (paths.data/'config').rglob('*.yml')}
                with profile_lock(paths.data):
                    try:
                        cold.call('configuration_options',timeout=60)
                        raise AssertionError('context initialization must respect profile lock')
                    except RemoteError:rejected+=1
                assert all(p.read_bytes()==raw for p,raw in before.items())
            finally:cold.close()
            # A second worker must see domain-saved lists and scalars from disk.
            other = OneDragonClient(paths)
            try:
                other.start()
                again = other.call("describe_configuration",app_id="charge_plan",timeout=60)
                assert values(again)["plan_list"][0]["plan_times"] == 3
            finally: other.close()
            print(json.dumps(dict(python=hello["python"], applications=30, configurations=len(descriptions),
                                  fields=sum(len(d["fields"]) for d in descriptions.values()), rejected_requests=rejected,
                                  collections_saved=5, commission_isolated=True, game_started=False),ensure_ascii=False))
        except Exception:
            print("\n".join(logs[-25:]),file=sys.stderr)
            raise
        finally: client.close()


if __name__ == "__main__": main()
