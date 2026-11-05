"""Offline native developer-page/source persistence and real process lifecycle acceptance."""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import time

DEVELOPMENT_ROOT=Path(__file__).resolve().parents[1]
def package_argument():
    if '--package'not in sys.argv:return None
    return Path(sys.argv[sys.argv.index('--package')+1]).resolve()

PACKAGE=package_argument()
if PACKAGE is None:sys.path.insert(0,str(DEVELOPMENT_ROOT))
else:
    # Test fixture lives in the checkout; all product imports must come from the staged bridge.
    sys.path[:]=[p for p in sys.path if p and (Path(p).resolve().is_relative_to(PACKAGE/'onedragon')
        or not Path(p).resolve().is_relative_to(DEVELOPMENT_ROOT))]
    sys.path.insert(0,str(PACKAGE/'onedragon/bridge'))


def child(source,data,package=None):
    sys.path.insert(0,str(source/'src'))
    # Source ScreenArea imports its logger immediately; bind data before any such source import.
    from one_dragon.utils import os_utils
    os_utils.set_work_dir(data)
    import cv2
    import numpy as np
    from PySide6.QtWidgets import QApplication
    from autozzz.onedragon_devtools_host import create_window
    from autozzz.onedragon_service import OneDragonService
    from autozzz.run_lock import RunLock,profile_lock
    from one_dragon.base.geometry.point import Point
    from one_dragon.base.screen.screen_info import ScreenInfo
    from one_dragon.base.screen.screen_area import ScreenArea
    from one_dragon.base.geometry.rectangle import Rect
    checks=[];events=[];runtime={}
    if package is not None:
        import shutil
        bundle=package/'onedragon'
        modules=('autozzz.onedragon_devtools_host','autozzz.onedragon_service','one_dragon.base.screen.screen_info','numpy','PySide6')
        origins={name:str(Path(sys.modules[name].__file__).resolve())for name in modules}
        assert all(Path(path).is_relative_to(bundle)for path in origins.values()),origins
        assert all(Path(path).resolve().is_relative_to(bundle)for path in sys.path if path),sys.path
        assert Path(sys.executable).resolve()==(bundle/'python/python.exe').resolve()
        assert shutil.which('git')is None,'Git must be hidden for package acceptance'
        assert 'PYTHONPATH'not in __import__('os').environ
        runtime=dict(executable=sys.executable,origins=origins,sys_path=sys.path.copy(),git_available=False,
            developer_pythonpath_hidden=True,package=str(package))
    class NoGameLease:
        def check(self):pass
        def scope(self):raise AssertionError('Offline acceptance must never request game capture')
    app=QApplication(['AutoZZZ offline devtools']);window=None
    service=OneDragonService(source,data)
    with RunLock(),profile_lock(data):
        try:
            ctx=service._create_context();window=create_window(ctx,data,'cn',NoGameLease(),events.append)
            window.show();app.processEvents()
            assert len(window.pages)==3
            analysis,templates,screens=window.pages
            assert len(analysis.logic.get_available_step_names())==21
            image=np.zeros((1080,1920,3),np.uint8);image[:,:,0]=123;image[:,:,1]=75;image[:,:,2]=21
            assert analysis.logic.load_image_from_array(image)
            analysis.logic.add_step('灰度化');actual,_=analysis.logic.execute_pipeline()
            assert np.array_equal(actual,cv2.cvtColor(cv2.cvtColor(image,cv2.COLOR_RGB2GRAY),cv2.COLOR_GRAY2RGB))
            cv=ctx.cv_service;assert cv.save_pipeline('自定义流水线',analysis.logic.pipeline)
            assert len(cv.load_pipeline('自定义流水线').steps)==1
            cv.rename_pipeline('自定义流水线','改名流水线');assert cv.load_pipeline('改名流水线')
            cv.delete_pipeline('改名流水线');assert cv.load_pipeline('改名流水线')is None
            window.guard.tx.restore(window.guard.tx.list()[0]['id']);assert cv.load_pipeline('改名流水线')
            contour=np.array([[[1,2]],[[10,2]],[[10,20]]],np.int32)
            assert cv.save_template_contour('自定义轮廓',contour)
            assert np.array_equal(cv.load_template_contour('自定义轮廓'),contour)
            cv.rename_template_contour('自定义轮廓','新轮廓');cv.delete_template_contour('新轮廓')
            checks.append('all_21_source_cv_steps_listed_real_grayscale_pipeline_contour_crud_restore')
            window.tabs.setCurrentIndex(1);app.processEvents();templates._on_create_clicked()
            template=templates.chosen_template;template.sub_dir='autozzz_fixture';template.template_id='unknown_asset'
            template.point_list=[Point(12,18),Point(88,72)];template.screen_image=image;template.point_updated=True
            templates._on_save_config_clicked();templates._on_save_raw_clicked();templates._on_save_mask_clicked()
            folder=data/'assets/template/autozzz_fixture/unknown_asset'
            assert np.array_equal(cv2.cvtColor(cv2.imread(str(folder/'raw.png')),cv2.COLOR_BGR2RGB),image[18:72,12:88])
            assert cv2.imread(str(folder/'mask.png'),cv2.IMREAD_GRAYSCALE).shape==(54,76)
            template.template_id='renamed_asset';templates._on_save_config_clicked()
            assert not folder.exists()and (folder.with_name('renamed_asset')/'raw.png').exists()
            templates._on_delete_clicked();assert not folder.with_name('renamed_asset').exists()
            window.guard.tx.restore(window.guard.tx.list()[0]['id']);assert folder.with_name('renamed_asset').exists()
            checks.append('native_unknown_template_create_source_crop_mask_rename_delete_archive_restore')
            window.tabs.setCurrentIndex(2);app.processEvents();screens._on_create_clicked()
            screen=screens.chosen_screen;screen.screen_id='autozzz_fixture';screen.screen_name='自定义画面';screen.pc_alt=True
            screen.area_list=[ScreenArea(area_name='自定义文字',pc_rect=Rect(10,20,200,80),text='测试',id_mark=True,gamepad_key='A',goto_list=['自定义画面'])]
            screens._on_save_clicked();assert (data/'assets/game_data/screen_info/autozzz_fixture.yml').exists()
            assert ctx.screen_loader.get_area('自定义画面','自定义文字').text=='测试'
            screen.screen_id='autozzz_renamed';screens._on_save_clicked()
            assert not (data/'assets/game_data/screen_info/autozzz_fixture.yml').exists()
            screens._on_delete_clicked();assert not (data/'assets/game_data/screen_info/autozzz_renamed.yml').exists()
            window.guard.tx.restore(window.guard.tx.list()[0]['id'])
            ctx.screen_loader.reload(from_separated_files=True);assert ctx.screen_loader.get_area('自定义画面','自定义文字').text=='测试'
            checks.append('native_screen_text_area_pc_alt_gamepad_route_create_rename_delete_restore')
            for operation in (lambda:cv.save_pipeline('../outside',analysis.logic.pipeline),lambda:cv.save_template_contour('CON',contour),
                              lambda:ctx.screen_loader.save_screen(ScreenInfo(dict(screen_id='../outside',screen_name='bad')))):
                try:operation()
                except ValueError:pass
                else:raise AssertionError('Source path escape must be rejected')
            try:ctx.controller.click(1,1)
            except RuntimeError:pass
            else:raise AssertionError('Native tools must not expose physical input')
            checks.append('source_mutation_path_escape_and_controller_input_rejected')
            # Exercise native navigation in default and minimum geometry at three Qt scale factors via parent subprocesses.
            for width,height in ((1400,900),(800,600)):
                window.resize(width,height)
                for index in range(3):window.tabs.setCurrentIndex(index);app.processEvents();assert window.pages[index].isVisible()
            checks.append('three_native_pages_default_minimum_layout')
            from unittest.mock import patch
            from PySide6.QtWidgets import QMessageBox
            cv.save_pipeline('中断回归',analysis.logic.pipeline)
            interrupted=data/'assets/image_analysis_pipelines/中断回归.yml';before=interrupted.read_bytes()
            class ProcessDeath(BaseException):pass
            def fail():interrupted.write_bytes(b'partially published');raise ProcessDeath()
            try:window.guard.tx.mutate([interrupted],fail,'sdk_interrupted_fixture')
            except ProcessDeath:pass
            review=window.guard.tx.interruption();assert review['pending']and not review['conflicts']
            with patch.object(QMessageBox,'exec',return_value=QMessageBox.No):window.recover_interrupted()
            assert interrupted.read_bytes()==b'partially published'and window.guard.tx.edit_journal.exists()
            def confirm(dialog):
                assert '中断回归.yml'in dialog.detailedText()and '已发布'in dialog.detailedText()
                return QMessageBox.Yes
            with patch.object(QMessageBox,'exec',confirm):window.recover_interrupted()
            assert interrupted.read_bytes()==before and not window.guard.tx.interruption()['pending']and window.closed
            checks.append('native_interrupted_recovery_dialog_hash_preview_cancel_then_verified_restore')
        finally:
            if window is not None:window.close();window.guard.__exit__(None,None,None)
            if service._ctx is not None:service._ctx.controller=None
            service.close();app.processEvents()
    print(json.dumps(dict(actual_sdk=True,checks=checks,runtime=runtime,game_initialized=False,game_started=False,game_input_sent=False),ensure_ascii=False))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--child',action='store_true');parser.add_argument('--source',type=Path);parser.add_argument('--data',type=Path)
    parser.add_argument('--package',type=Path,help='Use only this full staged package runtime, source and bridge.')
    args=parser.parse_args()
    if args.child:return child(args.source,args.data,args.package.resolve()if args.package else None)
    from autozzz.config import ROOT
    from autozzz.onedragon_runtime import RuntimePaths
    from autozzz.onedragon_devtools import DevtoolsSession
    import os
    def environment():
        env=os.environ.copy();env.update(PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1')
        if args.package:
            for key in ('PYTHONPATH','PYTHONHOME','PYTHONUSERBASE','VIRTUAL_ENV','CONDA_PREFIX','PYTHONSTARTUP','AUTOZZZ_INTERNAL_RUN_LEASE'):
                env.pop(key,None)
            windows=Path(env.get('SystemRoot',r'C:\Windows'))
            env['PATH']=os.pathsep.join(map(str,(args.package.resolve()/'onedragon/python',windows/'System32',windows)))
        return env
    if args.package:
        if not (args.package/'autozzz-package.json').is_file():raise ValueError('Stage package is incomplete: autozzz-package.json is absent.')
        for module in ('autozzz.onedragon_runtime','autozzz.onedragon_devtools'):
            assert Path(sys.modules[module].__file__).resolve().is_relative_to(args.package.resolve()/'onedragon/bridge')
    with tempfile.TemporaryDirectory(prefix='autozzz-native-devtools-')as temporary:
        base=Path(temporary);results=[]
        for factor in ('1','1.5','2'):
            paths=replace(RuntimePaths.discover(args.package.resolve()if args.package else ROOT,use_selection=False),data=base/factor)
            paths.prepare();env=environment();env['QT_SCALE_FACTOR']=factor
            command=[str(paths.python),'-I','-s','-P','-B',str(Path(__file__).resolve()),'--child','--source',str(paths.source),'--data',str(paths.data)]
            if args.package:command+=['--package',str(args.package.resolve())]
            result=subprocess.run(command,cwd=base,capture_output=True,text=True,encoding='utf-8',errors='replace',env=env,timeout=180)
            if result.returncode:raise RuntimeError(result.stdout+'\n'+result.stderr)
            rows=[row for row in result.stdout.splitlines()if row.startswith('{')]
            results.append(dict(scale=factor,result=json.loads(rows[-1])))
        from unittest.mock import patch
        with patch.dict(os.environ,environment(),clear=True):
            events=queue.Queue();session=DevtoolsSession(paths,events.put,'cn');ready=False;closed=False;errors=[]
            deadline=time.monotonic()+90
            try:
                while time.monotonic()<deadline:
                    event=events.get(timeout=10)
                    if event['type']=='ready':ready=True;session.close()
                    elif event['type']=='error':errors.append(event['message']);session.close()
                    elif event['type']=='closed':closed=True;break
            finally:session.close();session.worker.join(timeout=30)
        assert ready and closed and not session.worker.is_alive()and not errors,dict(ready=ready,closed=closed,errors=errors)
        print(json.dumps(dict(results=results,actual_process_open_close=True,worker_exited=True,game_input_sent=False,
            package=str(args.package.resolve())if args.package else None),ensure_ascii=False))


if __name__=='__main__':main()
