"""Capture shareable, offline UI previews without user configuration or history."""
from pathlib import Path
import sys
import tempfile
import tkinter as tk
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from autozzz import windows
from autozzz.config import Settings
from autozzz.gui import AutoZZZWindow
from PIL import ImageGrab


def main():
    output = Path(__file__).resolve().parents[1] / "docs" / "images"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        config = Path(directory) / "preview.json"
        Settings(game_path=r"C:\Games\ZenlessZoneZero\ZenlessZoneZero.exe").save(config)
        root = tk.Tk()
        root.tk.call("tk", "scaling", 96/72)
        with patch("autozzz.gui.AutoZZZWindow._discover_report"), patch("autozzz.battle_ui.ROOT", Path(directory)):
            ui = AutoZZZWindow(root, config)
        try:
            ui.overlay.set_enabled(False)
            root.update()
            for page, name in (("home", "compendium"), ("settings", "settings")):
                ui._show_page(page)
                root.update()
                ImageGrab.grab(window=root.winfo_id()).save(output / (name+".png"))
        finally:
            ui.closing = True
            ui.overlay.close()
            for token in root.tk.splitlist(root.tk.call("after", "info")):
                root.after_cancel(token)
            root.destroy()
    print("Saved sanitized offline previews:", output)


if __name__ == "__main__":
    main()
