"""Presentation preferences for the isolated Qt settings/tool processes only."""
from __future__ import annotations
from pathlib import Path
import gettext


def apply_native_preferences():
    from PySide6.QtCore import QLocale
    from PySide6.QtGui import QColor,QPalette,QFont,QFontDatabase
    from PySide6.QtWidgets import QApplication
    from qfluentwidgets import FluentTranslator,Theme,setTheme,setThemeColor
    from one_dragon.utils import i18_utils
    from .appearance_preferences import effective_appearance
    from .localization import ui_language,tr

    appearance=effective_appearance()
    language=ui_language()
    locale=QLocale('en_US' if language=='en' else 'zh_CN')
    QLocale.setDefault(locale)
    i18_utils.update_default_lang(language)
    if language=='en':
        class NativeUITranslations(gettext.NullTranslations):
            def gettext(self,message):return tr(message)
        # Extend only the source UI domain in this UI-only process. Captured gt
        # function aliases still use this dictionary; game/OCR domains stay intact.
        i18_utils._gt.setdefault('ui',{})['en']=NativeUITranslations()
    setTheme(Theme.LIGHT if appearance['mode']=='light' else Theme.DARK)
    setThemeColor(appearance['accent'])
    app=QApplication.instance()
    if app is None:raise RuntimeError('原生外观需要已创建的 Qt 应用。')
    # Both native hosts use the same bundled complete face as the main UI.
    # Offscreen Qt may have no Windows font fallback; never rely on a stub face.
    families=getattr(app,'_autozzz_font_families',None)
    if families is None:
        families=[]
        base=Path(__file__).resolve().parents[1]
        roots=(base/'assets/ui',base.parent.parent/'_internal/assets/ui')
        for root in roots:
            file=root/'inpin.ttf'
            if file.is_file():
                identifier=QFontDatabase.addApplicationFont(str(file))
                if identifier>=0:families.extend(QFontDatabase.applicationFontFamilies(identifier))
                break
        families=list(dict.fromkeys(families+['Microsoft YaHei UI','Segoe UI']))
        app._autozzz_font_families=families
    font=QFont();font.setFamilies(families);font.setPixelSize(14);app.setFont(font)
    from qfluentwidgets import setFontFamilies
    setFontFamilies(families,save=False)
    previous=getattr(app,'_autozzz_translator',None)
    if previous is not None:
        app.removeTranslator(previous);previous.deleteLater()
    translator=FluentTranslator(locale,app);app.installTranslator(translator)
    app._autozzz_translator=translator
    light=appearance['mode']=='light'
    colors=dict(window='#efefed' if light else '#343434',field='#ffffff' if light else '#161616',
                text='#20201e' if light else '#eeeeeb',muted='#60605c' if light else '#b0b0ad')
    palette=app.palette()
    for role,color in ((QPalette.Window,colors['window']),(QPalette.WindowText,colors['text']),
                       (QPalette.Base,colors['field']),(QPalette.AlternateBase,colors['window']),
                       (QPalette.Text,colors['text']),(QPalette.Button,colors['window']),
                       (QPalette.ButtonText,colors['text']),(QPalette.Highlight,appearance['accent']),
                       (QPalette.HighlightedText,appearance['accent_text'])):
        palette.setColor(role,QColor(color))
    for role in (QPalette.Text,QPalette.ButtonText,QPalette.WindowText):
        palette.setColor(QPalette.Disabled,role,QColor(colors['muted']))
    app.setPalette(palette)
    return appearance|colors|dict(language=language,font_families=families)
