"""UI presentation language; never changes game/OCR translations or config values."""
from __future__ import annotations
from contextlib import contextmanager
from contextvars import ContextVar
import ctypes
import gettext
from pathlib import Path
import sys
import threading

_language=None
_catalog=None
_lock=threading.RLock()
_override=ContextVar('autozzz_ui_language',default=None)


def system_ui_language():
    try:
        language=ctypes.windll.kernel32.GetUserDefaultUILanguage()
        return 'zh'if language&0x3ff==4 else'en'
    except (AttributeError,OSError):return 'en'


def ui_language(value=None):
    """Resolve auto/zh/en; the default preference is read once per process."""
    global _language
    scoped=_override.get()
    if value is None and scoped is not None:return scoped
    if value is None:
        with _lock:
            if _language is None:
                from .appearance_preferences import effective_preferences
                _language=ui_language(effective_preferences()['language'])
            return _language
    if value not in ('auto','zh','en'):raise ValueError('UI language must be auto, zh or en.')
    return system_ui_language()if value=='auto'else value


@contextmanager
def language_scope(language):
    """Presentation-only scoped override for layout tests and previews."""
    token=_override.set(ui_language(language))
    try:yield
    finally:_override.reset(token)


def _english_catalog():
    global _catalog
    with _lock:
        if _catalog is not None:return _catalog
        from .localization_catalog import EN_UI
        from .localization_native import EN_NATIVE
        catalog={}
        base=Path(__file__).resolve().parents[1]
        candidates=[base/'vendor/onedragon',base.parent/'source']
        try:
            from .config import ROOT
            candidates += [ROOT/'onedragon/source',ROOT/'vendor/onedragon']
        except ImportError:pass
        candidates += [Path(entry).parent for entry in sys.path if entry and Path(entry).name=='src']
        for source in candidates:
            file=source/'assets/text/output/en/LC_MESSAGES/ui.mo'
            if file.is_file():
                with file.open('rb')as stream:upstream=gettext.GNUTranslations(stream)
                catalog.update({key:value for key,value in upstream._catalog.items()if isinstance(key,str)and key and isinstance(value,str)})
                break
        catalog.update(EN_UI);catalog.update(EN_NATIVE)
        _catalog=catalog
        return catalog


def tr(text,**format_values):
    """Translate a complete UI phrase, then interpolate untouched user values."""
    if not isinstance(text,str):return text
    translated=text
    if ui_language()=='en':
        catalog=_english_catalog()
        translated=catalog.get(text,text)
        if translated==text and text.endswith(('…','...')):
            suffix='…'if text.endswith('…')else'...'
            base=text[:-len(suffix)]
            if base in catalog:translated=catalog[base]+suffix
    return translated.format(**format_values)if format_values else translated


APP_LABELS={
    'one_dragon':'一条龙应用','_group':'执行顺序','teams':'预编队伍','battle_assistant':'战斗设置',
    'daily_signin':'每日签到','scratch_card':'签到（刮卡 / 盲盒）','hou_hou_bakery':'饼铺奖励',
    'coffee':'喝杯咖啡','random_play':'录像店营业','engagement_reward':'活跃度奖励',
    'ridu_weekly':'丽都周纪奖励','city_fund':'丽都城募奖励','email':'邮件奖励','redemption_code':'兑换码',
    'charge_plan':'体力刷本','notorious_hunt':'恶名狩猎','shiyu_defense':'式舆防卫战',
    'auto_battle':'战斗助手','dodge_assistant':'闪避助手','intel_board':'情报板','life_on_line':'真拿命验收',
    'lost_void':'迷失之地','withered_domain':'枯萎之都','suibian_temple':'随便观',
    'trigrams_collection':'八卦收集','world_patrol':'巡逻路线','drive_disc_dismantle':'驱动盘拆解',
    'commission_assistant':'委托助手','predefined_team_checker':'预编队伍检查','mouse_sensitivity_checker':'鼠标灵敏度检查',
    'operation_debug':'指令调试','screenshot_helper':'截图助手','notify':'通知',
    '_game_input':'键位与镜头','_environment':'运行环境','_models':'识别模型'}


def application_name(app_id,name,third_party=False):
    return tr(APP_LABELS[app_id])if not third_party and app_id in APP_LABELS else name
