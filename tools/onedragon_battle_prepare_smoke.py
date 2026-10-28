"""准备实际上游条件战斗和闪光模型，不绑定游戏、不启动指令。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from autozzz.config import GAME_PATHS
from autozzz.onedragon_runtime import OneDragonClient,RuntimePaths,RequestPending,RemoteError


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--data",type=Path,required=True)
    args=parser.parse_args()
    defaults=RuntimePaths.discover(Path(__file__).resolve().parents[1])
    client=OneDragonClient(RuntimePaths(defaults.source,defaults.python,args.data),log=lambda message:print(message,file=sys.stderr,flush=True))
    try:
        client.start()
        initial=client.call("initialize_execution",server="cn",game_path=GAME_PATHS["cn"],timeout=30)
        def wait(job):
            while True:
                state=client.call("execution_status",timeout=5)
                assert state["job_id"]==job
                if not state["busy"]:return state
                time.sleep(.2)
        state=wait(initial["job_id"])
        assert state["state"]=="ready",state
        prepared=client.call("prepare_application",app_id="auto_battle",timeout=30)
        state=wait(prepared["job_id"])
        assert state["state"]=="prepared" and state["prepared_app"]=="auto_battle",state
        probe=client.call("runtime_probe",timeout=30)
        assert probe["flash_model"] and probe["flash_model"]["providers"],probe
        assert not probe["window_bound"] and not probe["upstream_hotkeys_started"]
        for params in (dict(app_id="charge_plan"),dict(app_id="auto_battle",budget={}),dict(app_id="auto_battle",timeout_seconds=1)):
            try:
                client.call("start_application",**params)
                raise AssertionError("must reject unprepared or invalid execution")
            except RemoteError:pass
        print(json.dumps(dict(prepared=state,probe=probe,game_started=False),ensure_ascii=False))
    finally:
        while True:
            try:
                client.close(timeout=5)
                break
            except RequestPending:print("Waiting for original battle preparation worker",file=sys.stderr,flush=True)


if __name__=="__main__":main()
