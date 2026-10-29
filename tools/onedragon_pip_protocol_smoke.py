"""真实画中画专用 SDK 连接的偏好、关闭和数据隔离；始终关闭截图。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import queue
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from autozzz.config import GAME_PATHS
from autozzz.onedragon_runtime import RuntimePaths
from autozzz.onedragon_pip_session import PipSession


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--data',type=Path,required=True);args=parser.parse_args();data=args.data.resolve()
    if data.exists():raise ValueError('请使用新的隔离资料目录。')
    defaults=RuntimePaths.discover(Path(__file__).resolve().parents[1]);paths=RuntimePaths(defaults.source,defaults.python,data)
    session=PipSession(paths,'cn',GAME_PATHS['cn']);events=[]
    try:
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            if session.ready:break
            if not session.worker.is_alive():raise RuntimeError('画中画原连接提前结束。')
            time.sleep(.03)
        assert session.ready
        state=None
        while time.monotonic()<deadline:
            state=session.take_state()
            if state is not None:break
            time.sleep(.03)
        assert state and state['state']=='off' and not state['enabled'] and not state['visible']
        assert session.client._hello['input_enabled'] is False
        session.send('geometry',values=dict(width=620,x=12,y=34));session.close()
        session.worker.join(30)
        assert not session.worker.is_alive() and not session.client.alive
        while True:
            try:events.append(session.events.get_nowait())
            except queue.Empty:break
        assert not [item for item in events if item['type']=='error'],events
        saved=[item['settings']['values'] for item in events if item['type']=='settings']
        assert saved and saved[-1]==dict(enabled=False,width=620,x=12,y=34),saved
        assert events[-1]==dict(type='closed',exited=True)
    finally:
        session.close();session.worker.join(5)
    result=dict(actual_independent_sdk_process=True,preferences_saved_before_close=True,worker_exited=True,
                game_started=False,game_input_sent=False,capture_started=False)
    (data/'protocol-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
