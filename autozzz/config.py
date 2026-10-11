from __future__ import annotations

import json
import math
import ntpath
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from .characters import canonical_promoter_name

ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", ROOT))
CONFIG_PATH = ROOT / "config.json"
GAME_PATHS = {
    "cn": r"E:\miHoYo Launcher\games\ZenlessZoneZero Game\ZenlessZoneZero.exe",
    "global": r"E:\HoYoPlay\games\ZenlessZoneZero Game\ZenlessZoneZero.exe",
}


@dataclass
class Settings:
    server: str = "cn"
    game_path: str = GAME_PATHS["cn"]
    scratch: bool = True
    coffee: bool = True
    video: bool = True
    promoter: str = ""
    daily: bool = True
    auto_launch: bool = False
    action_delay: float = 1.2
    transition_timeout: int = 45
    ocr_confidence: float = 0.60

    def select_server(self, server: str) -> None:
        if server not in GAME_PATHS:
            raise ValueError("区服必须为 cn 或 global。")
        self.server, self.game_path = server, GAME_PATHS[server]

    def validate(self) -> None:
        self.promoter = canonical_promoter_name(self.promoter)
        if self.server not in GAME_PATHS:
            raise ValueError("请选择国服或国际服。")
        if not isinstance(self.game_path, str) or Path(self.game_path).name.casefold() != "zenlesszonezero.exe":
            raise ValueError("请选择 ZenlessZoneZero.exe 游戏程序。")
        for server, official in GAME_PATHS.items():
            if ntpath.normpath(self.game_path).casefold() == ntpath.normpath(official).casefold() and server != self.server:
                raise ValueError("区服与游戏路径不一致，请切换到对应区服。")
        for name in ("scratch", "coffee", "video", "daily", "auto_launch"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} 必须是布尔值。")
        for name in ("action_delay", "transition_timeout", "ocr_confidence"):
            value = getattr(self, name)
            if type(value) not in (float, int) or not math.isfinite(value):
                raise ValueError(f"{name} 必须是有限数值。")
        if not 0.3 <= self.action_delay <= 5 or not 10 <= self.transition_timeout <= 180 or not 0.4 <= self.ocr_confidence <= 0.99:
            raise ValueError("操作间隔、界面超时或 OCR 阈值超出允许范围。")
        if not any((self.scratch, self.coffee, self.video, self.daily)):
            raise ValueError("至少选择一项每日任务。")

    def save(self, path: Path = CONFIG_PATH) -> None:
        self.validate()
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> Settings:
        path = Path(path)
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("配置必须是 JSON 对象。")
        unknown = set(data) - set(cls.__dataclass_fields__)
        if unknown:
            raise ValueError(f"配置中存在未知字段：{', '.join(sorted(unknown))}")
        result = cls(**data)
        result.validate()
        return result
