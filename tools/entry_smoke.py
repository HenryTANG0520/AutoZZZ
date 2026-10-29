"""Opt-in live QA of the GUI launch button; never starts the daily operation."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
import tkinter as tk

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from autozzz import windows  # DPI awareness before Tk
from autozzz.config import ROOT, Settings
from autozzz.gui import AutoZZZWindow


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    output = ROOT / "runs" / "entry-smoke" / time.strftime("%Y%m%d_%H%M%S")
    output.mkdir(parents=True)
    settings = Settings.load(args.config)
    config = output / "config.json"
    settings.save(config)
    root = tk.Tk()
    ui = AutoZZZWindow(root,config,background_helpers=False)
    started = time.monotonic()
    observed = {"overlay_visible": False, "focus_preserved": True}
    requested = False

    def check():
        nonlocal requested
        if ui.overlay.visible:
            observed["overlay_visible"] = True
            observed["focus_preserved"] &= windows.user32.GetForegroundWindow() == ui.overlay_game_handle
        if time.monotonic()-started > 240 and not requested:
            ui._stop()
            requested = True
        if ui.worker is not None and not ui.busy:
            report = ui.runner.report.to_dict() if ui.runner is not None else None
            if ui.last_report_file and ui.last_report_file.exists():
                report = json.loads(ui.last_report_file.read_text(encoding="utf-8"))
            proof = dict(observed, status=ui.status.get(), report=report, timeout=requested,
                         task_rows={name: list(ui.table.item(name, "values")) for name in ui.tasks})
            (output / "proof.json").write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding="utf-8")
            ui._close()
            return
        root.after(100, check)

    root.after(700, ui.launch_button.invoke)
    root.after(900, check)
    root.mainloop()


if __name__ == "__main__":
    main()
