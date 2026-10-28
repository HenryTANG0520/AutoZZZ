"""Offline interaction checks for live card data, navigation and report proof."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import tkinter as tk
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from autozzz import windows  # DPI only; no game calls.
from autozzz.config import Settings
from autozzz.gui import AutoZZZWindow
from ui_layout_smoke import check_layout


def main():
    checks = []
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "config.json"
        Settings().save(path)
        before = path.read_bytes()
        root = tk.Tk()
        def callback_error(kind, value, traceback):
            raise value.with_traceback(traceback)
        root.report_callback_exception = callback_error
        with patch("autozzz.gui.AutoZZZWindow._discover_report"):
            ui = AutoZZZWindow(root, path)
        try:
            ui.overlay.set_enabled(False)
            root.update()
            board = ui.table
            assert ui.engagement_meter.progress is None
            checks.append("unverified_progress_is_unknown")
            ui.engagement.set("活跃度 200 · 每日未验证")
            root.update()
            assert ui.engagement_meter.progress == 200
            checks.append("partial_engagement_updates_without_verified_claim")
            details = "已选择宣传员并确认开店。" * 30
            ui._task(dict(task="video", status="failed", detail=details))
            root.update()
            x1, y1, x2, y2 = board.hitboxes["video"]
            board.event_generate("<Button-1>", x=round((x1+x2)*board.scale/2), y=round((y1+y2)*board.scale/2))
            root.update()
            assert board.selection() == ("video",)
            assert ui.detail_text.get("1.0", "end-1c") == details
            assert ui.detail_window.winfo_height() <= round(430*ui.ui_scale)
            ui.detail_window.destroy()
            root.update()
            checks.append("click_opens_full_untruncated_live_detail")
            # Reach the three optional/reward cards through the actual arrow hit area.
            for _ in range(3):
                x1, y1, x2, y2 = board.hitboxes["next"]
                board.event_generate("<Button-1>", x=round((x1+x2)*board.scale/2), y=round((y1+y2)*board.scale/2))
                root.update()
            assert board.visible_tasks == ["daily", "weekly", "city_fund"]
            board.scroll(99)
            assert board.offset == 3
            board.move_selection(-6)
            root.update()
            assert board.selection() == ("scratch",) and board.offset == 0
            board.move_selection(6)
            root.update()
            assert board.selection() == ("city_fund",) and board.offset == 3
            checks.append("all_six_tasks_reachable_by_pointer_and_keyboard_navigation")
            for name, button in ui.nav_buttons.items():
                button.invoke()
                root.update()
                assert ui.current_page == name and ui.pages[name].winfo_ismapped()
                assert str(button.cget("style")) == "Selected.Nav.TButton"
                check_layout(root)
            checks.append("tabs_switch_pages_and_selected_state")
            ui._show_page("tasks")
            root.update()
            for name, control in ui.task_controls.items():
                original = ui.tasks[name].get()
                control.invoke()
                root.update()
                assert ui.tasks[name].get() != original, name
                assert ui.dirty and path.read_bytes() == before
                control.invoke()
                root.update()
                assert ui.tasks[name].get() == original, name
            checks.append("regrouped_task_controls_preserve_task_binding_and_draft")
            ui._show_page("settings")
            for key, button in ui.settings_buttons.items():
                button.invoke()
                root.update()
                assert ui.settings_sections[key].winfo_ismapped()
                check_layout(root)
            checks.append("all_settings_sections_and_editors_accessible")
            ui.close_game_control.invoke()
            ui.overlay_control.invoke()
            assert ui.dirty and path.read_bytes() == before
            checks.append("settings_edit_is_draft_only")
            ui.busy = True
            ui._refresh_controls()
            old = ui.close_game_on_success.get()
            ui.close_game_control.invoke()
            assert ui.close_game_on_success.get() == old
            checks.append("native_controls_cannot_edit_during_run")
            assert path.read_bytes() == before
        finally:
            ui.closing = True
            ui.overlay.close()
            for token in root.tk.splitlist(root.tk.call("after", "info")):
                root.after_cancel(token)
            root.destroy()
    print(json.dumps(dict(game_started=False, config_unchanged=True, count=len(checks), checks=checks), ensure_ascii=False))


if __name__ == "__main__":
    main()
