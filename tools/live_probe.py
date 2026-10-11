"""One observed action for calibration; records before/after, never reports a daily as done."""
import argparse
import json
import sys
import threading
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from autozzz.app import Context, Runner
from autozzz.config import ROOT, Settings
from autozzz.models import AutomationError
from autozzz.vision import Vision
from autozzz.windows import find_game

parser = argparse.ArgumentParser()
parser.add_argument("action", choices=("observe", "map", "handbook", "back", "click"))
parser.add_argument("--label")
parser.add_argument("--output", type=Path)
args = parser.parse_args()
if args.output:
    args.output.parent.mkdir(parents=True, exist_ok=True)
    sys.stdout = args.output.open("w", encoding="utf-8", buffering=1)
    sys.stderr = sys.stdout
elif hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
settings = Settings.load()
runner = Runner(settings, lambda event: print(json.dumps(event, ensure_ascii=False), flush=True))
runner.report.run_dir = str(ROOT / "runs" / ("probe_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f")))
runner._window = find_game(settings.game_path)
runner._window.focus()
runner._window.baseline = runner._window.guard()
threading.Thread(target=runner._hotkeys, daemon=True).start()
context = Context(runner, runner._window, Vision(settings.ocr_confidence))
try:
    before = context.snapshot()
    context.evidence(before, "before")
    if args.action in ("map", "handbook"):
        if not before.has("F2", (0.6, 0, 1, 0.2), exact=True) or not before.has("Tab", exact=True):
            raise AutomationError("尚未确认可操作的街区 HUD。")
        context.key("m" if args.action == "map" else "f2", before)
    elif args.action == "back":
        if not before.has(("日常", "六分街", "咖啡", "经营状况")):
            raise AutomationError("不是已识别的每日页面，拒绝返回键。")
        context.key("esc", before)
    elif args.action == "click":
        token = before.find(args.label or "", exact=True)
        if not token:
            raise AutomationError("没有识别到唯一准确目标。")
        matches = [t for t in before.tokens if t.text == token.text]
        if len(matches) != 1:
            raise AutomationError("目标存在歧义。")
        context.click(before, token)
    context.sleep(2)
    after = context.snapshot()
    path = context.evidence(after, "after")
    print(json.dumps({"screenshot": str(path), "ocr_text": after.text}, ensure_ascii=False, indent=2))
finally:
    runner._monitor_end.set()
