"""独立 Python 3.11 主线程承载上游 AppSettingManager。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import threading


def create_window(ctx,app_id,group_id):
    from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLabel
    from qfluentwidgets import PushButton
    from .native_preferences import apply_native_preferences
    from .localization import tr
    appearance=apply_native_preferences()
    from one_dragon_qt.services.app_setting.app_setting_manager import AppSettingManager
    from one_dragon_qt.widgets.pivot_navi_interface import PivotNavigatorInterface
    from one_dragon_qt.widgets.base_interface import BaseInterface

    class Home(BaseInterface):
        def __init__(self):
            super().__init__('autozzz_plugin_settings_home',tr('设置'),'')
            layout=QVBoxLayout(self)
            self.open_button=PushButton(tr('打开专用设置'),self);layout.addWidget(self.open_button);layout.addStretch()
    class Navigator(PivotNavigatorInterface):
        def create_sub_interface(self):
            self.home=Home();self.add_sub_interface(self.home,enable_page_stack=True)
    class Window(QWidget):
        def closeEvent(self,event):
            self.navigator.on_interface_hidden()
            tip=getattr(self.manager,'active_flyout',None)
            if tip is not None:
                try:tip.close()
                except RuntimeError:pass
            super().closeEvent(event)
            from PySide6.QtWidgets import QApplication
            QApplication.instance().quit()
    window=Window();info=ctx.factory_manager.get_plugin_info(app_id)
    from PySide6.QtWidgets import QApplication
    available=QApplication.primaryScreen().availableGeometry()
    width,height=min(1050,available.width()-40),min(740,available.height()-60)
    window.setWindowTitle('AutoZZZ · '+info.app_name);window.resize(width,height);window.setMinimumSize(min(720,width),min(500,height))
    window.setStyleSheet('QWidget#autozzz_settings_window { background: '+appearance['window']+'; } QLabel { color: '+appearance['text']+'; }')
    window.setObjectName('autozzz_settings_window')
    layout=QVBoxLayout(window);layout.setContentsMargins(20,16,20,16);layout.setSpacing(16)
    header=QHBoxLayout();title=QLabel(info.app_name,window);title.setWordWrap(True);header.addWidget(title);header.addStretch()
    account=ctx.one_dragon_config.current_active_instance
    account_label=QLabel(f'{account.name} · {group_id}',window);account_label.setWordWrap(True);header.addWidget(account_label);layout.addLayout(header)
    window.navigator=Navigator('autozzz_settings',tr('设置'),'',window);layout.addWidget(window.navigator)
    footer=QHBoxLayout();window.back=PushButton(tr('返回'),window);window.back.setEnabled(False)
    window.back.clicked.connect(window.navigator.pop_setting_interface);footer.addWidget(window.back);footer.addStretch()
    window.close_button=PushButton(tr('关闭'),window);window.close_button.clicked.connect(window.close);footer.addWidget(window.close_button);layout.addLayout(footer)
    window.navigator.secondary_state_changed.connect(window.back.setEnabled)
    class Manager(AppSettingManager):
        active_flyout=None
        def _push_interface(self,interface_cls,parent,group_id):
            super()._push_interface(interface_cls,parent,group_id)
            from PySide6.QtCore import Qt
            from one_dragon_qt.widgets.fast_scroll_area import FastScrollArea
            # 上游纵向页的双列卡片可能宽于本窗口；保留原控件，开放横向滚动。
            for interface in self._interface_cache.values():
                for area in interface.findChildren(FastScrollArea):
                    if area.horizontalScrollBarPolicy()==Qt.ScrollBarAlwaysOff:
                        area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        def _show_flyout(self,flyout_cls,parent,group_id,target):
            # 上游旧气泡的延迟销毁可能清空新气泡的共享引用；另保留本窗口的实际返回对象。
            if self.active_flyout is not None:
                try:self.active_flyout.close()
                except RuntimeError:pass
            self.active_flyout=flyout_cls.show_flyout(ctx=self.ctx,group_id=group_id,target=target,parent=parent)
    window.manager=Manager(ctx);window.manager.discover()
    if app_id not in window.manager.settable_app_ids:raise ValueError('未能加载该应用的专用设置，请查看运行日志。')
    def open_setting():
        window.manager.show_app_setting(app_id,window.navigator,group_id,window.navigator.home.open_button)
    window.navigator.home.open_button.clicked.connect(open_setting)
    window.open_setting=open_setting
    return window


def run(source,data,target,emit,lease,after_show=None,*,runtime_revision=None,stable_data=None,product_root=None,runtime_generation=None,management_only=False):
    from PySide6.QtCore import QObject,Signal,QTimer
    from PySide6.QtWidgets import QApplication
    from .run_lock import profile_lock
    from .onedragon_service import OneDragonService
    from .onedragon_settings import validate_target
    app=QApplication.instance() or QApplication(['AutoZZZ'])
    app.setQuitOnLastWindowClosed(True)
    from .native_preferences import apply_native_preferences
    apply_native_preferences()
    service=OneDragonService(Path(source),Path(data),run_lease=lease,runtime_revision=runtime_revision,
        stable_data=stable_data,product_root=product_root,runtime_generation=runtime_generation,management_only=management_only)
    with profile_lock(data):
        try:
            ctx=service._create_context()
            if not isinstance(target,dict) or set(target)!={'app_id','instance_idx','revision','group_id'}:raise ValueError('专用设置目标无效。')
            validate_target(ctx,**target)
            window=create_window(ctx,target['app_id'],target['group_id'])
            class Closing(QObject):requested=Signal()
            closing=Closing();closing.requested.connect(window.close)
            def commands():
                for line in sys.stdin.buffer:
                    try:
                        if json.loads(line)=={'method':'close'}:break
                    except (ValueError,TypeError):break
                closing.requested.emit()
            threading.Thread(target=commands,name='AutoZZZ-Settings-Close',daemon=True).start()
            monitor=QTimer(window)
            def check_parent():
                try:
                    lease.check()
                    service.runtime_guard.check_current()
                except Exception as exc:emit(dict(type='error',message=str(exc)));window.close()
            monitor.timeout.connect(check_parent);monitor.start(500)
            def show_setting():
                try:
                    window.open_setting()
                    emit(dict(type='ready',app_id=target['app_id'],instance_idx=ctx.current_instance_idx,group_id=target['group_id']))
                    if after_show is not None:after_show(app,window,ctx)
                except Exception as exc:emit(dict(type='error',message=str(exc)));window.close()
            window.show();QTimer.singleShot(0,show_setting)
            result=app.exec()
            window.close();window.deleteLater();app.processEvents()
            return result
        finally:service.close()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path,required=True);parser.add_argument('--data',type=Path,required=True);parser.add_argument('--target',required=True)
    from .onedragon_worker import add_runtime_identity_arguments,verify_worker_identity
    add_runtime_identity_arguments(parser)
    args=parser.parse_args();output=sys.stdout;sys.stdout=sys.stderr
    def emit(event):output.write(json.dumps(event,ensure_ascii=False)+'\n');output.flush()
    lease=None
    try:
        if sys.version_info[:2]!=(3,11):raise RuntimeError('专用设置需要独立的 Python 3.11 环境。')
        from .task_lease import JoinedRunLease
        lease=JoinedRunLease.from_environment()
        if lease is None:raise RuntimeError('专用设置缺少任务锁。')
        # 先设置来源，Qt 模块和原生设置类才可导入。
        sys.path.insert(0,str(args.source.resolve()/'src'))
        revision=verify_worker_identity(args)
        return run(args.source,args.data,json.loads(args.target),emit,lease,runtime_revision=revision,
            stable_data=args.stable_data,product_root=args.product_root,runtime_generation=args.selection_generation,management_only=args.management_only)
    except Exception as exc:
        import traceback
        traceback.print_exc();emit(dict(type='error',message=str(exc)));return 1
    finally:
        if lease is not None:lease.close()


if __name__=='__main__':raise SystemExit(main())
