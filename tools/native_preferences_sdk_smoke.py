"""Actual Qt/source preference and layout checks; isolated fixture data, no game."""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def child(args):
    sys.path.insert(0,str(args.source/'src'))
    from one_dragon.utils import os_utils
    os_utils.set_work_dir(args.data)
    from autozzz import appearance_preferences
    prefs=dict(appearance_preferences.DEFAULTS,theme=args.theme,language=args.language,accent='#2255dd')
    with patch.object(appearance_preferences,'effective_preferences',return_value=prefs):
        from PySide6.QtWidgets import QApplication,QAbstractButton,QLabel
        from PySide6.QtGui import QPalette,QRawFont
        from autozzz.native_preferences import apply_native_preferences
        from autozzz.onedragon_service import OneDragonService
        from autozzz.onedragon_devtools_host import create_window
        from autozzz.localization import tr
        from autozzz.run_lock import profile_lock
        app=QApplication(['AutoZZZ native preference check'])
        appearance=apply_native_preferences()
        assert appearance['language']==args.language
        raw_font=QRawFont.fromFont(app.font())
        assert raw_font.isValid() and all(raw_font.glyphIndexesForString('AutoZZZ绝区零')),'Native font lacks required glyphs'
        service=OneDragonService(args.source,args.data)
        class Lease:
            def check(self):pass
            def scope(self):raise AssertionError('This acceptance must not capture a game')
        with profile_lock(args.data):
            ctx=service._create_context()
            assert ctx.controller is None
            window=create_window(ctx,args.data,'cn',Lease(),lambda _:None)
            try:
                window.show();app.processEvents()
                palette=app.palette()
                assert palette.color(QPalette.Window).name()==appearance['window']
                assert palette.color(QPalette.Highlight).name()==appearance['accent']
                assert palette.color(QPalette.HighlightedText).name()==appearance['accent_text']
                assert 'Developer tools' in window.windowTitle() if args.language=='en' else '开发工具' in window.windowTitle()
                for index,label in enumerate(('图像分析','模板管理','画面管理')):
                    assert window.tabs.tabText(index)==tr(label)
                dimensions=[]
                for width,height in ((1500,950),(800,600)):
                    window.resize(width,height);app.processEvents()
                    assert window.width()<=width and window.height()<=height,(window.size(),width,height)
                    for widget in window.findChildren(QAbstractButton):
                        if widget.isVisible() and widget.parent() is window:
                            assert widget.geometry().left()>=0 and widget.geometry().right()<window.width(),widget.text()
                            assert widget.fontMetrics().horizontalAdvance(widget.text())+12<=widget.width(),('Clipped button',widget.text(),widget.width())
                    dimensions.append([window.width(),window.height()])
                    images=args.output.with_suffix('');images.mkdir(parents=True,exist_ok=True)
                    image=images/f'{os.environ.get("QT_SCALE_FACTOR","1")}-{args.language}-{args.theme}-{width}x{height}.png'
                    assert window.grab().save(str(image))
                assert not getattr(ctx,'ready_for_application',False)
                return dict(language=args.language,theme=args.theme,palette=appearance,dimensions=dimensions,
                            source_pages=3,actual_qt=True,actual_sdk=True,game_input_sent=False)
            finally:
                window.close();window.guard.__exit__(None,None,None);window.deleteLater();app.processEvents()
                ctx.controller=None;service.close()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--child',action='store_true');parser.add_argument('--source',type=Path);parser.add_argument('--data',type=Path)
    parser.add_argument('--theme',choices=('dark','light'));parser.add_argument('--language',choices=('zh','en'))
    parser.add_argument('--output',type=Path,default=ROOT/'runs/native-preferences-sdk-20261105.json')
    args=parser.parse_args()
    if args.child:
        print(json.dumps(child(args),ensure_ascii=False));return
    args.output=args.output.resolve()
    from autozzz.onedragon_runtime import RuntimePaths
    with tempfile.TemporaryDirectory(prefix='autozzz-native-prefs-')as folder:
        paths=replace(RuntimePaths.discover(ROOT,use_selection=False),data=Path(folder)/'data')
        paths.prepare();results=[]
        for scale in ('1','1.5','2'):
            for language in ('zh','en'):
                for theme in ('dark','light'):
                    environment=os.environ.copy();environment.update(QT_SCALE_FACTOR=scale,QT_QPA_PLATFORM='offscreen',PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1')
                    command=[str(paths.python),'-I','-s','-P','-B',str(Path(__file__).resolve()),'--child',
                             '--source',str(paths.source),'--data',str(paths.data),'--theme',theme,'--language',language,'--output',str(args.output)]
                    run=subprocess.run(command,cwd=folder,env=environment,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=120)
                    if run.returncode:raise RuntimeError(run.stdout+'\n'+run.stderr)
                    rows=[json.loads(line)for line in run.stdout.splitlines()if line.startswith('{')]
                    results.append(dict(scale=scale,result=rows[-1]))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(dict(results=results,offscreen=True,processes_exited=True,game_input_sent=False),ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(cases=len(results),output=str(args.output),game_input_sent=False)))


if __name__=='__main__':main()
