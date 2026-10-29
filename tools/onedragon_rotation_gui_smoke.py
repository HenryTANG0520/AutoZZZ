"""轮换预检展示和实际账号窗口入口，不登录或操作游戏。"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys
import tempfile
import time
import tkinter as tk
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from autozzz import windows
from autozzz.config import Settings
from autozzz.gui import AutoZZZWindow
from autozzz.onedragon_accounts_ui import AccountsWindow
from autozzz.onedragon_rotation_ui import RotationPreviewDialog
from autozzz.onedragon_runtime import RuntimePaths
from onedragon_configuration_gui_smoke import layout,pump


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--plan',type=Path,required=True);parser.add_argument('--preview',type=Path);args=parser.parse_args()
    plan=json.loads(args.plan.read_text(encoding='utf-8'));cases,checks=0,[]
    with tempfile.TemporaryDirectory(prefix='autozzz-rotation-ui-') as folder:
        config=Path(folder)/'config.json';Settings().save(config);original=config.read_bytes()
        with patch('autozzz.gui.AutoZZZWindow._discover_report'):
            for scale in (1.,1.5,2.):
                root=tk.Tk();root.tk.call('tk','scaling',scale*96/72);errors=[]
                root.report_callback_exception=lambda kind,value,tb:errors.append(value)
                ui=AutoZZZWindow(root,config,background_helpers=False);ui.overlay.set_enabled(False);selected=[]
                dialog=RotationPreviewDialog(root,copy.deepcopy(plan),ui.ui_scale,selected.append)
                for size in ('default','minimum'):
                    if size=='minimum':dialog.window.geometry('%dx%d'%dialog.window.minsize())
                    root.update();layout(dialog.window);cases+=1
                dialog.table.selection_set('3');dialog.select();root.update()
                expected=next(row for row in plan['instances'] if row['idx']==3)['applications'][0]['name']
                assert expected in dialog.details.get('1.0','end')
                dialog.edit();assert selected==[3]
                started=[]
                dialog=RotationPreviewDialog(root,copy.deepcopy(plan),ui.ui_scale,selected.append,lambda:started.append(True))
                root.update();layout(dialog.window);cases+=1
                footer=dialog.window.winfo_children()[-1]
                next(child for child in footer.winfo_children() if child.cget('text')=='连续轮换…').invoke()
                assert started==[True] and not dialog.window.winfo_exists()
                broken=copy.deepcopy(plan);broken['configuration_valid']=False;broken['issues']=[dict(instance_idx=2,message='请填写登录资料。')]
                broken['instances'][0]['login_configured']=False
                dialog=RotationPreviewDialog(root,broken,ui.ui_scale,selected.append);dialog.table.selection_set('2');dialog.select();root.update()
                layout(dialog.window);cases+=1
                assert '请填写登录资料' in dialog.details.get('1.0','end')
                if args.preview and scale==1.:
                    from PIL import ImageGrab
                    # 等待 Windows 窗口淡入结束，避免把上一弹窗的过渡帧当作预览。
                    time.sleep(.35);root.update()
                    args.preview.parent.mkdir(parents=True,exist_ok=True)
                    box=(dialog.window.winfo_rootx(),dialog.window.winfo_rooty(),dialog.window.winfo_rootx()+dialog.window.winfo_width(),dialog.window.winfo_rooty()+dialog.window.winfo_height())
                    ImageGrab.grab(bbox=box).save(args.preview)
                dialog.window.destroy()
                single=copy.deepcopy(plan);single['instances']=[plan['instances'][0]];single['transitions']=[];single['uses_charge']=False
                dialog=RotationPreviewDialog(root,single,ui.ui_scale,selected.append);root.update();layout(dialog.window);cases+=1
                dialog.window.destroy();ui._close();assert not errors,errors
                checks.append(dict(scale=scale,distinct_account_apps=True,login_diagnostics=True,edit_selected_account=True,single_account_fallback=True))

            defaults=RuntimePaths.discover(Path(__file__).resolve().parents[1]);paths=RuntimePaths(defaults.source,defaults.python,Path(folder)/'actual-sdk')
            with patch('autozzz.onedragon_accounts_ui.RuntimePaths.discover',return_value=paths):
                root=tk.Tk();errors=[];root.report_callback_exception=lambda kind,value,tb:errors.append(value)
                ui=AutoZZZWindow(root,config,background_helpers=False);ui.overlay.set_enabled(False);accounts=AccountsWindow(ui,'cn');ui.account_window=accounts
                try:
                    pump(root,lambda:accounts.description is not None and not accounts.busy)
                    accounts.dirty=True;accounts.preview_rotation();assert accounts.preview_dialog is None and '先保存' in accounts.status.get()
                    accounts.dirty=False;accounts.update_buttons();accounts.preview_button.invoke()
                    pump(root,lambda:accounts.preview_dialog is not None and not accounts.busy)
                    dialog=accounts.preview_dialog;assert dialog.plan['instances'] and not dialog.plan['configuration_valid']
                    root.update();layout(dialog.window);layout(accounts.window);cases+=1
                    selected=dialog.plan['instances'][0]['idx'];dialog.edit();pump(root,lambda:not accounts.busy)
                    assert accounts.selected()==selected and not errors,errors
                    assert config.read_bytes()==original and not list(paths.data.rglob('*.onnx'))
                    accounts.preview_button.invoke();pump(root,lambda:accounts.preview_dialog.window.winfo_exists() and not accounts.busy)
                    launched=[]
                    def open_rotation(*args,**kwargs):
                        assert not accounts.worker.is_alive()
                        launched.append((args[1:4],kwargs))
                    with patch('autozzz.onedragon_run_ui.open_execution',side_effect=open_rotation):
                        accounts.run_rotation();pump(root,lambda:accounts.closed)
                    assert launched==[(('_rotation','多账号轮换','cn'),{})]
                    accounts.worker.join(2)
                    assert not accounts.worker.is_alive()
                    checks.append(dict(real_sdk_account_window_preview=True,unsaved_login_guard=True,close_destroys_preview_and_waits_worker=True,legacy_config_unchanged=True))
                finally:accounts.dirty=False;accounts.close();pump(root,lambda:accounts.closed);ui._close()
    print(json.dumps(dict(layout_cases=cases,checks=checks,game_accessed=False,rotation_executed=False),ensure_ascii=False),flush=True)


if __name__=='__main__':main()
