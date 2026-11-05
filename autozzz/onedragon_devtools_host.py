"""Native Qt main-thread host for three fixed-source, profile-confined developer pages."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import threading
import time


class ReadOnlyCaptureController:
    """Native pages can consume one verified frame; they cannot activate or input."""
    def __init__(self):self.frame=None
    def screenshot(self,*args,**kwargs):
        if self.frame is None:raise RuntimeError('请点击截图并将绝区零切到前台；不会自动激活游戏。')
        return time.time(),self.frame
    def __getattr__(self,name):raise RuntimeError('开发工具不提供游戏输入或启动控制：'+name)


def create_window(ctx,root,profile,lease,emit):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QGridLayout,QLabel,QTabWidget,QComboBox,QMessageBox
    from qfluentwidgets import PushButton
    from .native_preferences import apply_native_preferences
    from .localization import tr
    appearance=apply_native_preferences()
    from one_dragon_qt.view.devtools.devtools_image_analysis_interface import DevtoolsImageAnalysisInterface
    from one_dragon_qt.view.devtools.devtools_template_helper_interface import DevtoolsTemplateHelperInterface
    from one_dragon_qt.view.devtools.devtools_screen_manage_interface import DevtoolsScreenManageInterface
    from .devtools_resources import NativeResourceGuard
    from .config import GAME_PATHS

    controller=ReadOnlyCaptureController();ctx.controller=controller
    def deny(*args,**kwargs):raise RuntimeError('开发工具不初始化游戏控制器；请使用正常运行入口。')
    ctx.init_controller=deny
    guard=NativeResourceGuard(ctx,root,lease.check);guard.__enter__()
    class Window(QWidget):
        closed=False;capture_pending=None
        def closeEvent(self,event):
            self.closed=True;self.capture_pending=None
            for page in self.pages:page.on_interface_hidden()
            super().closeEvent(event)
    window=Window();window.guard=guard;window.setWindowTitle(tr('AutoZZZ · 开发工具'))
    from PySide6.QtWidgets import QApplication
    available=QApplication.primaryScreen().availableGeometry()
    window.resize(min(1500,available.width()-40),min(950,available.height()-70))
    window.setMinimumSize(min(800,available.width()-40),min(600,available.height()-70))
    layout=QVBoxLayout(window);layout.setContentsMargins(16,12,16,12)
    scope=QLabel(tr('{server} · 当前账号资料',server=tr('国际服'if profile=='global'else'国服')))
    scope.setWordWrap(True);layout.addWidget(scope)
    tabs=QTabWidget(window);layout.addWidget(tabs,1);window.tabs=tabs
    try:
        window.pages=[DevtoolsImageAnalysisInterface(ctx),DevtoolsTemplateHelperInterface(ctx),DevtoolsScreenManageInterface(ctx)]
        for page,label in zip(window.pages,('图像分析','模板管理','画面管理')):
            # Source VerticalScrollInterface builds some pages lazily on first show.
            page.on_interface_shown();page.on_interface_hidden();tabs.addTab(page,tr(label))
        status=QLabel(tr('导入图片，或在游戏前台截图（F10取消）。'));status.setWordWrap(True);layout.addWidget(status);window.status=status
        def message(text,error=False):
            status.setText(tr(text));emit(dict(type='error'if error else'status',message=text))
        window.message=message
        window.previous=0;window.pages[0].on_interface_shown()
        def changed(index):
            window.pages[window.previous].on_interface_hidden();window.pages[index].on_interface_shown();window.previous=index
        tabs.currentChanged.connect(changed)
        def start_capture(page):
            if window.closed:return
            if window.capture_pending is not None:message('已有截图请求等待前台；F10取消。');return
            window.capture_pending=(page,time.monotonic()+30)
            message('请将绝区零切到前台，等待截图（30秒）；F10取消。')
        def capture_tick():
            if window.closed or window.capture_pending is None:return
            page,deadline=window.capture_pending
            from .windows import user32
            if user32.GetAsyncKeyState(0x79)&0x8000:
                window.capture_pending=None;message('截图已取消。');return
            if time.monotonic()>deadline:
                window.capture_pending=None;message('截图超时；保持游戏前台后可重新请求。',True);return
            from .agent_template_capture import capture
            from .trace_capture import read_capture
            game_path=ctx.game_account_config.game_path or GAME_PATHS[profile]
            try:
                value=capture(root,game_path,lock=lease.scope());image,_=read_capture(root,value)
            except ValueError as exc:
                # Focus waiting is expected. Path, missing window and identity failures remain visible.
                if '前台'not in str(exc):status.setText(str(exc)+'；等待重新核实。')
                return
            except Exception as exc:
                window.capture_pending=None;message(str(exc),True);return
            window.capture_pending=None
            import cv2
            import numpy as np
            controller.frame=cv2.resize(np.asarray(image.convert('RGB')),(1920,1080),interpolation=cv2.INTER_AREA)
            try:page._on_screenshot_clicked();message('截图已载入；未向游戏发送输入。')
            except Exception as exc:message(str(exc),True)
            finally:controller.frame=None
        timer=QTimer(window);timer.timeout.connect(capture_tick);timer.start(250);window.capture_timer=timer
        for page in window.pages:
            page.screenshot_btn.clicked.disconnect()
            page.screenshot_btn.clicked.connect(lambda checked=False,page=page:start_capture(page))
        # The original pipeline includes OCR. Load only existing local models, on first OCR use.
        logic=window.pages[0].logic;execute=logic.execute_pipeline
        def execute_pipeline():
            if any(step.name=='OCR识别'for step in logic.pipeline.steps)and not getattr(window,'ocr_ready',False):
                from one_dragon.base.matcher.ocr.onnx_ocr_matcher import get_final_file_list,get_ocr_model_dict_name
                name=ctx.model_config.ocr
                if not get_ocr_model_dict_name(name)or not all(Path(p).is_file()for p in get_final_file_list(name)):
                    raise ValueError('OCR模型缺失，请关闭开发工具后在“资源下载”中安装所选模型。')
                ctx.init_ocr()
                if getattr(ctx.ocr,'_model',None)is None:raise ValueError('本地OCR模型未能加载，请检查资源与运行环境。')
                logic.cv_service.ocr=ctx.ocr;window.ocr_ready=True
            return execute()
        logic.execute_pipeline=execute_pipeline
        bar=QGridLayout();layout.addLayout(bar);bar.setColumnStretch(0,1);bar.setColumnStretch(1,1)
        archives=QComboBox(window);bar.addWidget(archives,0,0,1,2);window.archives=archives
        def refresh():
            archives.clear()
            for row in guard.tx.list():archives.addItem(row['metadata'].get('action','')+' · '+row['id'],row['id'])
        refresh_button=PushButton(tr('刷新归档'));refresh_button.clicked.connect(refresh);bar.addWidget(refresh_button,0,2)
        restore_button=PushButton(tr('恢复所选修改前版本'));bar.addWidget(restore_button,1,0)
        def restore():
            identifier=archives.currentData()
            if not identifier:return
            if QMessageBox.question(window,tr('恢复资源'),tr('恢复到所选操作之前的资料？当前文件变化时会拒绝覆盖。'))!=QMessageBox.Yes:return
            try:
                guard.tx.restore(identifier);message('旧版本已恢复；请关闭后重新打开开发工具，避免旧草稿覆盖。');window.close()
            except Exception as exc:message(str(exc),True)
        restore_button.clicked.connect(restore)
        recover_button=PushButton(tr('恢复中断修改…'));bar.addWidget(recover_button,1,1)
        def recover():
            try:
                review=guard.tx.interruption()
                if not review['pending']:message('没有待恢复的中断修改。');return
                dialog=QMessageBox(window);dialog.setWindowTitle(tr('核对中断修改'))
                dialog.setText(tr('涉及 {files} 个文件；冲突 {conflicts} 项。\n仅恢复仍匹配原版本或已发布版本的文件。',files=len(review['rows']),conflicts=len(review['conflicts'])))
                dialog.setDetailedText('\n\n'.join(row['path']+'\n原版本 '+row['old_hash']+'\n已发布 '+row['published_hash']+'\n当前 '+row['current_hash']+('\n冲突 '+row['conflict']if row['conflict']else'')for row in review['rows']))
                dialog.setStandardButtons(QMessageBox.Close if review['conflicts']else QMessageBox.Yes|QMessageBox.No)
                if dialog.exec()!=QMessageBox.Yes:return
                guard.tx.recover_edit(review);message('中断修改已恢复，归档保留。请重新打开开发工具。');window.close()
            except Exception as exc:message(str(exc),True)
        recover_button.clicked.connect(recover);window.recover_interrupted=recover
        close=PushButton(tr('关闭'));close.clicked.connect(window.close);bar.addWidget(close,1,2);refresh()
        return window
    except BaseException:
        ctx.controller=None;guard.__exit__(*sys.exc_info());raise


def run(source,data,profile,emit,lease,after_show=None,*,runtime_revision=None,stable_data=None,product_root=None,runtime_generation=None,management_only=False):
    from PySide6.QtCore import QObject,Signal,QTimer
    from PySide6.QtWidgets import QApplication
    from .onedragon_service import OneDragonService
    from .run_lock import profile_lock
    app=QApplication.instance()or QApplication(['AutoZZZ']);app.setQuitOnLastWindowClosed(True)
    service=OneDragonService(Path(source),Path(data),run_lease=lease,runtime_revision=runtime_revision,
        stable_data=stable_data,product_root=product_root,runtime_generation=runtime_generation,management_only=management_only);window=None
    with profile_lock(data):
        try:
            ctx=service._create_context();window=create_window(ctx,data,profile,lease,emit)
            class Closing(QObject):requested=Signal()
            closing=Closing();closing.requested.connect(window.close)
            def commands():
                for line in sys.stdin.buffer:
                    try:
                        if json.loads(line)=={'method':'close'}:break
                    except (ValueError,TypeError):break
                closing.requested.emit()
            threading.Thread(target=commands,name='AutoZZZ-Devtools-Close',daemon=True).start()
            monitor=QTimer(window)
            def check_parent():
                try:
                    lease.check()
                    service.runtime_guard.check_current()
                except Exception as exc:emit(dict(type='error',message=str(exc)));window.close()
            monitor.timeout.connect(check_parent);monitor.start(250)
            old_hook=sys.excepthook
            def exception(kind,error,trace):
                old_hook(kind,error,trace);window.message(str(error),True)
            sys.excepthook=exception
            try:
                window.show();emit(dict(type='ready',pages=['image_analysis','templates','screens'],game_initialized=False))
                if after_show is not None:QTimer.singleShot(0,lambda:after_show(app,window,ctx))
                return app.exec()
            finally:sys.excepthook=old_hook
        finally:
            if window is not None:
                window.close();window.guard.__exit__(None,None,None);window.deleteLater();app.processEvents()
            # Do not run source controller shutdown, which can release global physical inputs.
            if service._ctx is not None:service._ctx.controller=None
            service.close()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path,required=True);parser.add_argument('--data',type=Path,required=True)
    parser.add_argument('--profile',choices=['cn','global'],required=True)
    from .onedragon_worker import add_runtime_identity_arguments,verify_worker_identity
    add_runtime_identity_arguments(parser);args=parser.parse_args()
    output=sys.stdout;sys.stdout=sys.stderr
    def emit(event):output.write(json.dumps(event,ensure_ascii=False)+'\n');output.flush()
    lease=None
    try:
        if sys.version_info[:2]!=(3,11):raise RuntimeError('开发工具需要独立 Python 3.11 环境。')
        from .task_lease import JoinedRunLease
        lease=JoinedRunLease.from_environment()
        if lease is None:raise RuntimeError('开发工具缺少任务锁。')
        sys.path.insert(0,str(args.source.resolve()/'src'))
        revision=verify_worker_identity(args)
        return run(args.source,args.data,args.profile,emit,lease,runtime_revision=revision,
            stable_data=args.stable_data,product_root=args.product_root,runtime_generation=args.selection_generation,management_only=args.management_only)
    except Exception as exc:
        import traceback
        traceback.print_exc();emit(dict(type='error',message=str(exc)));return 1
    finally:
        if lease is not None:lease.close()


if __name__=='__main__':raise SystemExit(main())
