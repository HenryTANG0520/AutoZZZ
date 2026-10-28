"""Check actual widget containment at three DPIs without running a game."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import tkinter as tk

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from autozzz import windows  # Establish Windows DPI awareness before creating Tk.
from autozzz.config import Settings
from autozzz.gui import AutoZZZWindow
from autozzz.game_widgets import SettingRow
from autozzz.theme import bitmap_font


def check_layout(widget):
    if isinstance(widget, SettingRow) and widget.control and widget.control.winfo_ismapped():
        title_width = bitmap_font(round(17 * widget.scale * 2)).getlength(widget.title) / 2
        assert 20 * widget.scale + title_width <= widget.control.winfo_x() - 6 * widget.scale, (widget.title, "setting title overlaps editor")
    for child in widget.winfo_children():
        if not child.winfo_ismapped():
            continue
        bounds = (child.winfo_x(), child.winfo_y(), child.winfo_width(), child.winfo_height())
        x, y, width, height = bounds
        assert x >= -2 and y >= -2, (str(child), "negative position", bounds)
        assert x + width <= widget.winfo_width() + 2, (str(child), "outside parent width", bounds)
        assert y + height <= widget.winfo_height() + 2, (str(child), "outside parent height", bounds)
        if child.winfo_class() in ("TLabel", "TButton", "TCheckbutton"):
            assert width >= child.winfo_reqwidth(), (str(child), "text clipped horizontally")
            assert height >= child.winfo_reqheight(), (str(child), "text clipped vertically")
        check_layout(child)


def main():
    cases = []
    with tempfile.TemporaryDirectory() as folder:
        config = Path(folder) / "config.json"
        Settings().save(config)
        original = config.read_bytes()
        for scale in (1., 1.5, 2.):
            root = tk.Tk()
            def callback_error(kind, value, traceback):
                raise value.with_traceback(traceback)
            root.report_callback_exception = callback_error
            root.tk.call("tk", "scaling", scale * 96 / 72)
            ui = AutoZZZWindow(root, config)
            try:
                ui.overlay.set_enabled(False)
                for size in ("default", "minimum"):
                    if size == "minimum":
                        root.geometry("%dx%d" % root.minsize())
                    for name in ui.pages:
                        ui._show_page(name)
                        root.update()
                        try:
                            check_layout(root)
                        except AssertionError as exc:
                            raise AssertionError((scale, size, name, exc.args)) from exc
                        cases.append(dict(dpi=round(scale * 100), size=size, page=name))
                        if name == "settings":
                            for section in ("run", "recognition"):
                                ui.show_settings_section(section)
                                root.update()
                                check_layout(root)
                                cases.append(dict(dpi=round(scale*100), size=size, page=name, section=section))
                            ui.show_settings_section("game")
                assert config.read_bytes() == original, "Layout verification must not save settings"
            finally:
                ui.closing = True
                ui.overlay.close()
                for token in root.tk.splitlist(root.tk.call("after", "info")):
                    root.after_cancel(token)
                root.destroy()
    print(json.dumps(dict(game_started=False, config_unchanged=True, layout_cases=len(cases),
                          dpi=[100, 150, 200], pages=len(ui.pages), settings_sections=3), ensure_ascii=False))


if __name__ == "__main__":
    main()
