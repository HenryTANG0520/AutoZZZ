"""Bind the SDK's attack HUD observations to one captured frame."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any


_NORMAL_BAN = 'btn_ban_normal_attack'
_ULTIMATE_BAN = 'btn_ban_ultimate'


@dataclass(frozen=True, eq=False)
class AttackHudObservation:
    battle: Any
    screen: Any
    captured_at: float
    normal_available: bool
    normal_disabled: bool
    ultimate_disabled: bool

    @property
    def in_battle(self):
        return self.normal_available or self.normal_disabled

    @property
    def blocks_ordinary(self):
        return self.normal_disabled or self.ultimate_disabled

    def disabled_buttons(self, checks):
        known = {
            (id(self.battle.area_btn_normal), _NORMAL_BAN): self.normal_disabled,
            (id(self.battle.area_btn_ultimate), _ULTIMATE_BAN): self.ultimate_disabled,
        }
        matches = {}
        for name, area, template in checks:
            key = id(area), template
            if key in known:
                matches[name] = known[key]
            else:
                matches[name] = self.battle._match_btn_ban_gray(self.screen, area, template)
        return matches


_current_hud: ContextVar[AttackHudObservation | None] = ContextVar('autozzz_attack_hud', default=None)


def scoped_attack_hud(battle, screen):
    observation = _current_hud.get()
    if observation is not None and observation.battle is battle and observation.screen is screen:
        return observation
    return None


def observe_attack_hud(battle, screen, captured_at):
    observation = scoped_attack_hud(battle, screen)
    if observation is not None and observation.captured_at == captured_at:
        return observation
    # Drop an outer observation when a nested call provides a new capture time.
    with attack_hud_scope(None):
        available = battle.is_normal_attack_btn_available(screen)
        matcher = getattr(type(battle), '_match_btn_ban_gray', None)
        normal = ultimate = False
        if callable(matcher):
            normal = matcher(battle, screen, battle.area_btn_normal, _NORMAL_BAN)
            ultimate = matcher(battle, screen, battle.area_btn_ultimate, _ULTIMATE_BAN)
    return AttackHudObservation(battle, screen, captured_at, available, normal, ultimate)


@contextmanager
def attack_hud_scope(observation):
    token = _current_hud.set(observation)
    try:
        yield
    finally:
        _current_hud.reset(token)
