"""Validated Chinese character names for the video-store promoter preference.

This is a spelling catalogue, not an account's unlocked promoter list. The
workflow must still find a name on an available portrait card before selecting
it; otherwise it falls back to the first available card.

Reviewed 2026-10-11, game version 3.2. Agent short names were cross-checked with
OneDragon's game-data roster; full-name aliases and corrections use the official
character directory. The 3.2 release announcement confirms Claret and Roxy.
The two NPC card names are confirmed by the user's supplied promoter screenshot.
No OneDragon implementation code is copied here.

Sources:
https://zenless.hoyoverse.com/zh-cn/character/detail?id=155657
https://zzz.mihoyo.com/news/166026?nav=%E7%B1%B3%E5%93%88%E6%B8%B8%3Fnav%3D%E7%B1%B3%E5%93%88%E6%B8%B8
https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/main/src/zzz_od/game_data/agent.py
https://wiki.hoyolab.com/m/zzz/entry/618?lang=zh-cn
"""
from __future__ import annotations

from difflib import get_close_matches
from types import MappingProxyType
import unicodedata

CATALOG_VERSION = "3.2 (2026-10-11)"

# Canonical names are the short Chinese labels, where known. Alternate forms
# are explicit: a short substring must never turn 安比 into 零号·安比 or 比利
# into 星徽·比利. New characters should be added after source verification.
_NAME_ROWS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("安比", ("安比·德玛拉",)),
    ("安东", ("安东·伊万诺夫",)),
    ("本", ("本·比格",)),
    ("比利", ("比利·奇德",)),
    ("可琳", ("可琳·威克斯",)),
    ("艾莲", ("艾莲·乔",)),
    ("格莉丝", ("格莉丝·霍华德",)),
    ("珂蕾妲", ("珂蕾妲·贝洛伯格",)),
    ("露西", ()),
    ("莱卡恩", ("冯·莱卡恩",)),
    ("猫又", ("猫宫又奈",)),
    ("妮可", ("妮可·德玛拉",)),
    ("派派", ("派派·韦尔",)),
    ("丽娜", ("亚历山德丽娜·莎芭丝缇安",)),
    ("11号", ("「11号」",)),
    ("苍角", ()),
    ("朱鸢", ()),
    ("青衣", ()),
    ("简", ("简·杜",)),
    ("赛斯", ("赛斯·洛威尔",)),
    ("凯撒", ("凯撒·金",)),
    ("柏妮思", ("柏妮思·怀特",)),
    ("柳", ("月城柳",)),
    ("莱特", ()),
    ("悠真", ("浅羽悠真",)),
    ("雅", ("星见雅",)),
    ("耀嘉音", ()),
    ("伊芙琳", ("伊芙琳·舒瓦利耶",)),
    ("零号·安比", ("零号安比", "银心锡兵·安比", "银心锡兵安比")),
    ("波可娜", ("波可娜·费雷尼",)),
    ("扳机", ("「扳机」",)),
    ("薇薇安", ()),
    ("雨果", ()),
    ("仪玄", ()),
    ("潘引壶", ()),
    ("橘福福", ()),
    ("浮波柚叶", ("柚叶",)),
    ("爱丽丝", ("爱丽丝·泰姆菲尔德",)),
    ("席德", ("「席德」",)),
    ("奥菲丝", ("奥菲丝&「鬼火」",)),
    ("卢西娅", ()),
    ("真斗", ("狛野真斗",)),
    ("伊德海莉", ()),
    ("琉音", ()),
    ("般岳", ()),
    ("照", ()),
    ("千夏", ()),
    ("叶瞬光", ()),
    ("爱芮", ()),
    ("南宫羽", ()),
    ("希希芙", ()),
    ("普罗米娅", ()),
    ("维琳娜", ("维琳娜·艾嘉德",)),
    ("佩洛伊斯", ()),
    ("星徽·比利", ("星徽比利",)),
    ("诺姆", ("诺姆·霍洛维尔",)),
    ("蕾米埃尔", ("蕾米埃尔·丹",)),
    ("希格莉德", ("希格莉德·德拉叙尔",)),
    ("克拉蕾", ("克拉蕾·弗林特",)),
    ("洛克茜", ("洛克茜·伊芙莉塔·普莱斯",)),
    # Actual promoter cards, not playable agents (user's 2026-10-11 screenshot).
    ("丝琪", ()),
    ("邦布18", ()),
)

CHARACTER_NAMES = tuple(name for name, _ in _NAME_ROWS)


def _normalise(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).strip()
    return value.translate(str.maketrans({"・": "·", "･": "·", "‧": "·", "•": "·"}))


def _build_lookup() -> dict[str, str]:
    lookup: dict[str, str] = {}
    for name, aliases in _NAME_ROWS:
        for label in (name, *aliases):
            key = _normalise(label)
            if key in lookup and lookup[key] != name:
                raise RuntimeError(f"宣传员名单别名冲突：{label}")
            lookup[key] = name
    return lookup


_LOOKUP = MappingProxyType(_build_lookup())
_ALIASES = MappingProxyType({name: (name, *aliases) for name, aliases in _NAME_ROWS})


def canonical_promoter_name(value: str) -> str:
    """Return a validated label, or ``''`` for the default-first preference.

    Suggestions are explanatory only; a misspelling is never automatically
    converted to another character or used as a fuzzy match in game.
    """
    if not isinstance(value, str):
        raise ValueError("宣传员名字必须是文字；留空默认选择第一位。")
    normalised = _normalise(value)
    if not normalised:
        return ""
    if normalised in _LOOKUP:
        return _LOOKUP[normalised]
    suggestions = get_close_matches(normalised, _LOOKUP, n=2, cutoff=0.6)
    names = tuple(dict.fromkeys(_LOOKUP[item] for item in suggestions))
    hint = f"是否想输入：{'、'.join(names)}？" if names else "请填写角色名，或留空默认第一位。"
    raise ValueError(f"宣传员名字不在内置名单中。{hint}")


def promoter_name_aliases(value: str) -> tuple[str, ...]:
    """Known exact card-name variants for one validated preference."""
    canonical = canonical_promoter_name(value)
    return _ALIASES[canonical] if canonical else ()
