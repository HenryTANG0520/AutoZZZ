"""Installation-local update preferences, separate from daily/game settings."""
from __future__ import annotations
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import uuid

from .schedules import revision
from .run_lock import profile_lock


@dataclass(frozen=True)
class UpdatePreferences:
    enabled: bool = False
    channel: str = 'stable'

    def validate(self):
        if type(self.enabled) is not bool or self.channel not in ('stable', 'beta'):
            raise ValueError('自动检查更新设置无效。')


class UpdatePreferencesStore:
    def __init__(self, root: Path, path: Path | None = None):
        key=revision(str(Path(root).resolve()).casefold())[:24]
        self.path=Path(path) if path is not None else Path(os.environ.get('LOCALAPPDATA',str(Path.home()/'.local/share')))/'AutoZZZ/program-update-preferences'/key/'settings.json'
        self.path=self.path.absolute()

    def check_path(self):
        if self.path!=self.path.resolve() or self.path.exists() and self.path.stat().st_nlink!=1:
            raise ValueError('更新偏好路径被重定向或共享。')

    def load(self):
        self.check_path()
        if not self.path.exists():return UpdatePreferences()
        if self.path.stat().st_size>4096:raise ValueError('更新偏好文件过大。')
        value=json.loads(self.path.read_text(encoding='utf-8'))
        if type(value) is not dict or set(value)!={'enabled','channel'}:raise ValueError('更新偏好格式无效。')
        result=UpdatePreferences(**value);result.validate();return result

    def save(self, value, expected):
        value.validate();self.check_path();self.path.parent.mkdir(parents=True,exist_ok=True)
        with profile_lock(self.path.parent):
            if self.load()!=expected:raise ValueError('更新偏好已在其他窗口改变，请重新打开助手。')
            temporary=self.path.with_name(self.path.name+'.'+uuid.uuid4().hex+'.tmp')
            try:
                temporary.write_text(json.dumps(asdict(value),ensure_ascii=False),encoding='utf-8');self.check_path();os.replace(temporary,self.path)
            finally:temporary.unlink(missing_ok=True)
