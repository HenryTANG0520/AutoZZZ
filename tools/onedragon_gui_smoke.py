"""原生策略编辑窗口与真实上游进程的离线集成检查。"""
from __future__ import annotations

import json
import argparse
from pathlib import Path
import sys
import tempfile
import time
import tkinter as tk
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from autozzz import windows
from autozzz.config import Settings
from autozzz.gui import AutoZZZWindow
from autozzz.onedragon_runtime import RuntimePaths
from autozzz.onedragon_ui import open_strategies
from ui_layout_smoke import check_layout


def pump(root, predicate, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        root.update()
        if predicate():
            return
        time.sleep(.02)
    raise AssertionError("条件策略界面未在检查时间内完成")


def button(dialog, label):
    return next(control for control in dialog.controls if control.winfo_class() == "TButton" and control.cget("text") == label)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--preview", type=Path)
    args = parser.parse_args()
    root_path = Path(__file__).resolve().parents[1]
    default = RuntimePaths.discover(root_path)
    checks = []
    with tempfile.TemporaryDirectory(prefix="autozzz-policy-ui-") as directory:
        folder = Path(directory)
        config = folder / "config.json"
        Settings().save(config)
        before = config.read_bytes()
        paths = RuntimePaths(default.source, default.python, folder / "onedragon")
        imported = folder / "自定义条件策略.yml"
        imported.write_text('author: AutoZZZ\nscenes:\n  - handlers:\n      - states: ""\n        operations:\n          - op_name: 等待秒数\n            seconds: 0.01\n', encoding="utf-8")
        for scale in (1., 1.5, 2.):
            root = tk.Tk()
            root.report_callback_exception = lambda kind, value, tb: (_ for _ in ()).throw(value.with_traceback(tb))
            root.tk.call("tk", "scaling", scale * 96 / 72)
            with patch("autozzz.gui.AutoZZZWindow._discover_report"), patch("autozzz.onedragon_ui.RuntimePaths.discover", return_value=paths):
                ui = AutoZZZWindow(root, config)
                ui.overlay.set_enabled(False)
                open_strategies(ui)
                dialog = ui.condition_strategies
            try:
                pump(root, lambda: not dialog.busy)
                assert "全配队通用" in dialog.names and dialog.editor.get("1.0", "end-1c").strip(), dialog.status.get()
                if args.preview and scale == 1:
                    from PIL import ImageGrab
                    args.preview.parent.mkdir(parents=True, exist_ok=True)
                    ImageGrab.grab(window=dialog.window.winfo_id()).save(args.preview)
                for size in ("default", "minimum"):
                    if size == "minimum":
                        dialog.window.geometry("%dx%d" % dialog.window.minsize())
                    root.update()
                    check_layout(dialog.window)
                    checks.append(f"{round(scale*100)}_{size}_layout")
                if scale == 1:
                    with patch("autozzz.onedragon_ui.filedialog.askopenfilename", return_value=str(imported)):
                        button(dialog, "导入 YAML…").invoke()
                    root.update()
                    assert dialog.dirty and dialog.name.get() == imported.stem
                    policies = paths.data / "config" / "auto_battle"
                    before_files = {p.name for p in policies.iterdir()}
                    button(dialog, "校验草稿").invoke()
                    pump(root, lambda: not dialog.busy)
                    assert "校验通过" in dialog.status.get(), dialog.status.get()
                    assert {p.name for p in policies.iterdir()} == before_files
                    assert dialog.dirty
                    checks.append("draft_validation_does_not_save")
                    button(dialog, "保存策略").invoke()
                    pump(root, lambda: not dialog.busy)
                    assert not dialog.dirty and "已保存" in dialog.status.get(), dialog.status.get()
                    assert (policies / imported.name).read_bytes() == imported.read_bytes()
                    button(dialog, "设为条件战斗默认").invoke()
                    pump(root, lambda: not dialog.busy)
                    assert dialog.status.get() == "已设为条件战斗默认策略", dialog.status.get()
                    checks.append("import_save_select_real_upstream_policy")
                    dialog.name.set("未读取的新策略")
                    button(dialog, "设为条件战斗默认").invoke()
                    assert "请先读取或保存" in dialog.status.get() and not dialog.busy
                    checks.append("name_change_cannot_select_unread_content")
                    dialog.name.set(dialog.loaded_name)
                    previous=dialog.editor.get('1.0','end-1c');dialog.dirty=True;dialog.kind.set('闪避')
                    with patch('autozzz.onedragon_ui.messagebox.askokcancel',return_value=False):dialog.change_kind()
                    assert dialog.kind.get()=='自动战斗' and dialog.policy_kind=='auto_battle' and dialog.dirty
                    assert dialog.editor.get('1.0','end-1c')==previous
                    dialog.dirty=False;dialog.kind.set('闪避')
                    with patch.object(dialog,'_initial',side_effect=RuntimeError('fixture load failed')):
                        dialog.change_kind();pump(root,lambda:not dialog.busy)
                    assert dialog.policy_kind=='auto_battle' and dialog.kind.get()=='自动战斗' and dialog.editor.get('1.0','end-1c')==previous
                    checks.append('kind_change_cancel_and_error_preserve_current_draft')
                dialog.kind.set('闪避');dialog.change_kind();pump(root,lambda:not dialog.busy)
                assert dialog.policy_kind=='dodge' and '闪避' in dialog.names and str(dialog.kind_selector.cget('state'))=='readonly',(dialog.policy_kind,dialog.names,dialog.kind_selector.cget('state'),dialog.status.get())
                for size in ('default','minimum'):
                    if size=='minimum':dialog.window.geometry('%dx%d' % dialog.window.minsize())
                    root.update();check_layout(dialog.window);checks.append(f'{round(scale*100)}_{size}_dodge_layout')
                if scale==1:
                    dialog.name.set('自定义闪避策略');button(dialog,'保存策略').invoke();pump(root,lambda:not dialog.busy)
                    assert (paths.data/'config/dodge/自定义闪避策略.yml').is_file()
                    assert not (paths.data/'config/auto_battle/自定义闪避策略.yml').exists()
                    button(dialog,'设为闪避默认').invoke();pump(root,lambda:not dialog.busy)
                    assert dialog.status.get()=='已设为闪避默认策略'
                    if args.preview:
                        from PIL import ImageGrab
                        ImageGrab.grab(window=dialog.window.winfo_id()).save(args.preview.with_name('dodge.png'))
                    checks.append('dodge_save_and_select_actual_upstream')
                dialog.kind.set('自动战斗');dialog.change_kind();pump(root,lambda:not dialog.busy)
                assert dialog.policy_kind=='auto_battle' and dialog.name.get()==imported.stem
                assert config.read_bytes() == before
                assert dialog.worker is not None and not dialog.worker.is_alive()
                if scale == 2:
                    button(dialog, "读取").invoke()
                    assert dialog.busy
                    assert dialog.close() and dialog.closing and not dialog.closed
                    pump(root, lambda: dialog.closed)
                    assert not dialog.worker.is_alive()
                    checks.append("close_waits_for_original_process")
                else:
                    dialog.close()
                    root.update()
                    assert dialog.closed
            finally:
                ui.closing = True
                if not dialog.closed:
                    dialog.dirty=False;dialog.close();pump(root,lambda:dialog.closed)
                ui.overlay.close()
                for token in root.tk.splitlist(root.tk.call("after", "info")):
                    root.after_cancel(token)
                root.destroy()
    print(json.dumps(dict(checks=checks, daily_config_unchanged=True, game_started=False), ensure_ascii=False))


if __name__ == "__main__":
    main()
