"""Offline combat UI dispatch check. Runner is replaced before creating the GUI."""
from __future__ import annotations
import hashlib
import argparse
import json
from pathlib import Path
import sys
import tempfile
import time
import tkinter as tk
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from autozzz.config import Settings
from autozzz import windows  # Set DPI awareness before Tk; no input is sent.
from autozzz.gui import AutoZZZWindow


class FakeRunner:
    calls=[]
    def __init__(self,settings,emit):
        self._window=None
    def stop(self):pass
    def run(self,**kwargs):
        self.calls.append(kwargs)
        operation='battle_catalog' if kwargs['catalog_only'] else 'battle_prepare' if kwargs['prepare_only'] else 'battle'
        return SimpleNamespace(to_dict=lambda:dict(operation=operation,status='success',stamina_spent=0,
                                                   battle={},results=[],run_dir=''))


def pump(root,until,timeout=5):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        root.update()
        if until():return
        time.sleep(.02)
    raise AssertionError('Mock GUI timed out')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--screenshots',type=Path);args=parser.parse_args()
    if args.screenshots:args.screenshots.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory() as directory,patch('autozzz.app.Runner',FakeRunner):
        config=Path(directory)/'config.json';Settings().save(config)
        before=hashlib.sha256(config.read_bytes()).hexdigest()
        root=tk.Tk();ui=AutoZZZWindow(root,config,background_helpers=False)
        try:
            ui.overlay.set_enabled(False)
            ui.combat_panel.vars['strategy_file'].set('builtin:cn-team1')
            ui._show_page('combat');root.update()
            assert ui.combat_panel.tabs.index(ui.combat_panel.tabs.select())==0
            if args.screenshots:
                from PIL import ImageGrab
                ImageGrab.grab(window=root.winfo_id()).save(args.screenshots/'conditional.png')
            opened=[]
            with patch('autozzz.onedragon_run_ui.open_execution',side_effect=lambda *args:opened.append(args[1:])):
                for server,profile in (('国服','cn'),('国际服','global')):
                    ui.server.set(server)
                    for app_id,button in ui.combat_panel.upstream_buttons.items():
                        button.invoke();assert opened[-1][0]==app_id and opened[-1][-1]==profile
            assert len(opened)==18 and not FakeRunner.calls
            ui.server.set('国服');ui.combat_panel.tabs.select(ui.combat_panel.legacy_page);root.update()
            if args.screenshots:ImageGrab.grab(window=root.winfo_id()).save(args.screenshots/'legacy.png')
            for operation in ('battle_prepare','battle','battle_catalog'):
                ui._start(operation)
                pump(root,lambda:not ui.busy and ui.worker_done)
                assert ui.worker is not None and not ui.worker.is_alive()
                assert FakeRunner.calls[-1]['prepare_only']==(operation=='battle_prepare')
                assert FakeRunner.calls[-1]['catalog_only']==(operation=='battle_catalog')
                if operation!='battle_catalog':
                    assert FakeRunner.calls[-1]['battle_plan'].strategy_file=='builtin:cn-team1'
                assert not ui.running
                root.deiconify();root.update()
            assert hashlib.sha256(config.read_bytes()).hexdigest()==before,'Starting combat must not save daily settings'
            print(json.dumps(dict(mock_backend=True,game_started=False,operations=len(FakeRunner.calls),
                                  strategy_dispatch=True,daily_config_unchanged=True)))
        finally:
            ui.closing=True;ui.overlay.close();root.destroy()


if __name__=='__main__':main()
