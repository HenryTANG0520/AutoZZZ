"""实际准备探索应用及其模型、候选策略；不绑定游戏或执行应用。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from autozzz.config import GAME_PATHS
from autozzz.onedragon_runtime import OneDragonClient,RequestPending,RuntimePaths


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--data",type=Path,required=True)
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    marker=args.data/".autozzz-exploration-test"
    if args.data.exists() and any(args.data.iterdir()) and not marker.is_file():
        raise SystemExit("测试会修改临时队伍配置，请使用空目录或本工具已有的测试目录。")
    args.data.mkdir(parents=True,exist_ok=True)
    marker.write_text("isolated-test-profile",encoding="utf-8")
    cached=root/"runs/onedragon-20261028/initialize-cn/assets/models"
    if cached.is_dir():shutil.copytree(cached,args.data/"assets/models",dirs_exist_ok=True)
    defaults=RuntimePaths.discover(root)
    paths=RuntimePaths(defaults.source,defaults.python,args.data.resolve())
    client=OneDragonClient(paths,log=lambda message:print(message,file=sys.stderr,flush=True))
    def call(method,**params):
        request=client.submit(method,**params)
        while True:
            try:return client.result(request,timeout=5)
            except RequestPending:pass
    def wait(job):
        last=None
        while True:
            state=call("execution_status")
            assert state["job_id"]==job,state
            phase=state.get("preparation_step") or state["state"]
            if phase!=last:
                print(json.dumps(dict(phase=phase,job=job),ensure_ascii=False),flush=True)
                last=phase
            if not state["busy"]:return state
            time.sleep(.2)
    try:
        client.start()
        state=wait(call("initialize_execution",server="cn",game_path=GAME_PATHS["cn"])["job_id"])
        assert state["state"]=="ready",state
        fixture="AutoZZZ-探索准备测试"
        call("policy_save",name=fixture,content='author: AutoZZZ\nscenes:\n  - handlers:\n      - states: ""\n        operations:\n          - op_name: 等待秒数\n            seconds: 0.01\n')
        description=call("describe_configuration",app_id="teams")
        teams=next(field["value"] for field in description["fields"] if field["name"]=="team_list")
        teams[-1]["auto_battle"]=fixture
        call("save_configuration",app_id="teams",values=dict(team_list=teams),revision=description["revision"])
        results={}
        for app in ("intel_board","lost_void","withered_domain","shiyu_defense","world_patrol"):
            state=wait(call("prepare_application",app_id=app)["job_id"])
            assert state["state"]=="prepared" and state["prepared_app"]==app,state
            assert not state["uses_charge"],state
            results[app]=state["prepared_requirements"]
        assert fixture in [item["name"] for item in results["shiyu_defense"]["policies"]],results
        assert results["world_patrol"]["routes"]
        description=call("describe_configuration",app_id="_group")
        rows=next(field["value"] for field in description["fields"] if field["name"]=="app_list")
        targets=set(results)
        assert targets<={row["app_id"] for row in rows}
        for row in rows:row["enabled"]=row["app_id"] in targets
        call("save_configuration",app_id="_group",values=dict(app_list=rows),revision=description["revision"])
        state=wait(call("prepare_application",app_id="one_dragon")["job_id"])
        assert state["state"]=="prepared" and not state["uses_charge"],state
        assert {item["id"] for item in state["prepared_applications"]}==targets,state
        assert set(state["prepared_requirements"]["models"])=={"lost_void_det","hollow_zero_event","flash_classifier"},state
        results["one_dragon"]=state["prepared_requirements"]
        probe=call("runtime_probe")
        assert "400" in probe["ocr_text"],probe
        assert set(probe["detectors"])=={"lost_void_det","hollow_zero_event"},probe
        assert all(model["providers"] for model in probe["detectors"].values()),probe
        assert not probe["window_bound"] and not probe["upstream_hotkeys_started"],probe
        print(json.dumps(dict(prepared=results,probe=probe,game_started=False),ensure_ascii=False),flush=True)
    finally:
        while True:
            try:client.close(timeout=5);break
            except RequestPending:print("Waiting for original preparation worker",file=sys.stderr,flush=True)


if __name__=="__main__":main()
