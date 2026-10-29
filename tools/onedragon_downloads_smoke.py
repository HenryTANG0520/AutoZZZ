"""实际多源下载、进度、取消与安装恢复回放；只传输内存测试包。"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import io
import json
import logging
import os
from pathlib import Path
import platform
import sys
import threading
import time
import urllib.error
import zipfile
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--data',required=True,type=Path);args=parser.parse_args()
    root=args.data.resolve();marker=root/'.autozzz-downloads-test'
    if root.exists() and any(root.iterdir()) and not marker.is_file():raise SystemExit('请使用空目录或本工具拥有的目录。')
    root.mkdir(parents=True,exist_ok=True);marker.write_text('isolated download test',encoding='utf-8')
    from autozzz.onedragon_runtime import RuntimePaths
    defaults=RuntimePaths.discover(Path(__file__).resolve().parents[1]);RuntimePaths(defaults.source,defaults.python,root).prepare()
    platform.uname();sys.path.insert(0,str(defaults.source/'src'))
    from autozzz.onedragon_service import OneDragonService
    from autozzz.onedragon_environment import EnvironmentStore
    from autozzz.onedragon_execution import OneDragonExecution
    from one_dragon.utils.log_utils import log
    checks=[];service=OneDragonService(defaults.source,root)
    def forbidden(*_,**__):raise AssertionError('模型下载回放禁止实际外部操作')
    class Transfer:
        def __init__(self):
            self.data=b'';self.calls=[];self.fail=0;self.block=False
            self.seen=threading.Event();self.release=threading.Event();self.message='fixture network failure'
        def open(self,request,timeout):
            assert timeout==5;self.calls.append(request.full_url)
            if self.fail:self.fail-=1;raise urllib.error.URLError(self.message)
            transfer=self
            class Response:
                def __init__(self):self.headers={'Content-Length':str(len(transfer.data))};self.stream=io.BytesIO(transfer.data)
                def __enter__(self):return self
                def __exit__(self,*_):self.stream.close()
                def read(self,count):
                    transfer.seen.set()
                    if transfer.block:
                        assert transfer.release.wait(5),'fixture read was not released'
                    return self.stream.read(count)
            return Response()
    transfer=Transfer()
    def pack(required,ocr=False):
        output=io.BytesIO()
        with zipfile.ZipFile(output,'w') as archive:
            for name in required:archive.writestr(name,os.urandom(128*1024) if name.endswith('.onnx') else b'fixture labels\n')
            if ocr:archive.writestr('fixture_dict.txt',b'fixture characters\n')
        return output.getvalue()
    def finish(job):
        service._downloads.thread.join(10)
        result=service.model_download_status(job['job_id']);assert not result['busy'],result
        return result
    with ExitStack() as blocks:
        for name in ('subprocess.Popen','requests.sessions.Session.request','socket.socket.connect','socket.socket.connect_ex','socket.getaddrinfo','winreg.SetValueEx','winreg.DeleteValue','autozzz.windows.find_game','pynput.keyboard.Controller.press','pynput.mouse.Controller.press'):
            blocks.enter_context(patch(name,side_effect=forbidden))
        blocks.enter_context(patch('urllib.request.build_opener',return_value=transfer))
        try:
            catalog=service.model_catalog();assert len(catalog['items'])==7 and not catalog['pending']
            assert service.ctx.controller is None and service.ctx.ocr._model is None and not service.ctx._btn_listener_started
            initial_catalog=catalog;checks.append('actual_seven_model_versions_and_release_sources_without_model_or_controller_initialization')
            row=next(row for row in catalog['items'] if row['family']=='flash_classifier' and row['recommended'])
            transfer.data=pack(row['required']);transfer.block=True
            target=service._downloads.install.target(row['family'],row['model']);(target/'old.txt').write_bytes(b'original model fixture')
            catalog=service.model_catalog();execution=object.__new__(OneDragonExecution);execution.data=root
            previous_digest=execution.configuration_digest()
            job=service.model_download_start(row['id'],catalog['revision']);assert transfer.seen.wait(3)
            original_thread=service._downloads.thread
            try:service.model_download_start(row['id'],catalog['revision']);raise AssertionError('active job was duplicated')
            except RuntimeError as exc:assert '下载' in str(exc)
            observed=service.model_download_status(job['job_id']);assert observed['busy'] and observed['job_id']==job['job_id']
            transfer.block=False;transfer.release.set();result=finish(job);assert result['state']=='success' and result['installed'],result
            assert service._downloads.thread is original_thread and not (target/'old.txt').exists()
            previous=service._downloads.install.archive_dir(job['job_id'])/'previous/old.txt';assert previous.read_bytes()==b'original model fixture'
            assert execution.configuration_digest()!=previous_digest
            assert service._environment_changed
            try:service.initialize_execution('global','fixture.exe');raise AssertionError('cached model reused')
            except RuntimeError as exc:assert '重新连接' in str(exc)
            checks.append('actual_common_downloader_progress_same_job_install_preserves_previous_directory_and_invalidates_prepared_process')

            catalog=service.model_catalog();backup=next(row for row in catalog['items'] if row['family']=='flash_classifier' and not row['recommended'])
            transfer.data=pack(backup['required']);transfer.seen.clear();transfer.release.clear();transfer.block=True
            job=service.model_download_start(backup['id'],catalog['revision']);assert transfer.seen.wait(3)
            status=service.model_download_cancel(job['job_id']);assert status['state']=='cancelling' and status['busy']
            assert service._downloads.thread.is_alive();transfer.release.set();transfer.block=False
            result=finish(job);assert result['state']=='cancelled' and not result['installed'],result
            assert not any(service._downloads.install.target(backup['family'],backup['model']).iterdir())
            checks.append('actual_http_cancel_keeps_original_thread_until_read_finishes_and_never_publishes_partial_model')

            catalog=service.model_catalog();transfer.data=pack(backup['required']);transfer.fail=1;count=len(transfer.calls)
            job=service.model_download_start(backup['id'],catalog['revision']);result=finish(job)
            assert result['state']=='success' and len(transfer.calls)==count+2,result
            assert transfer.calls[-1]!=transfer.calls[-2]
            checks.append('actual_source_order_falls_back_once_after_network_failure_and_extracts_verified_files')

            catalog=service.model_catalog();ocr=next(row for row in catalog['items'] if row['family']=='onnx_ocr')
            transfer.data=pack(ocr['required'],ocr=True)
            result=finish(service.model_download_start(ocr['id'],catalog['revision']));assert result['state']=='success',result
            assert next(row for row in service.model_catalog()['items'] if row['id']==ocr['id'])['ready']
            checks.append('actual_ocr_release_parameters_require_models_font_and_exactly_one_dictionary')

            catalog=service.model_catalog();bad=next(row for row in catalog['items'] if row['family']=='hollow_zero_event')
            transfer.data=pack(['model.onnx']);target_bad=service._downloads.install.target(bad['family'],bad['model']);before=service._downloads.install.digest(target_bad)
            result=finish(service.model_download_start(bad['id'],catalog['revision']));assert result['state']=='failed' and '缺少' in result['detail'],result
            assert service._downloads.install.digest(target_bad)==before
            checks.append('incomplete_package_is_rejected_before_touching_original_model')

            environment=service.describe_configuration('_environment')
            service.save_configuration('_environment',{'proxy_type':'personal','personal_proxy':'http://fixture-user:fixture-secret@127.0.0.1:18888'},environment['revision'])
            catalog=service.model_catalog();transfer.fail=3;transfer.message='http://fixture-user:fixture-secret@127.0.0.1:18888'
            captured=io.StringIO();handler=logging.StreamHandler(captured);log.addHandler(handler)
            try:result=finish(service.model_download_start(bad['id'],catalog['revision']))
            finally:log.removeHandler(handler)
            assert result['state']=='failed' and 'fixture-secret' not in captured.getvalue()+json.dumps(result)
            checks.append('actual_fallback_failures_and_exception_logs_redact_saved_proxy_credentials')
            count=len(transfer.calls)
            try:service.model_download_start(bad['id'],'stale');raise AssertionError('stale config accepted')
            except ValueError as exc:assert '变化' in str(exc)
            assert len(transfer.calls)==count
            checks.append('stale_model_or_source_revision_rejected_before_network_call')

            tx=service._downloads.install;identifier='a'*32;stage=tx.job_dir(identifier)/'content';stage.mkdir(parents=True,exist_ok=False)
            (stage/'model.onnx').write_bytes(b'fixture replacement');move=tx.move
            def crash(origin,target):
                if origin==stage:raise SystemExit('fixture interrupted installation')
                move(origin,target)
            with patch.object(tx,'move',side_effect=crash):
                try:tx.publish(bad['family'],bad['model'],identifier);raise AssertionError('interruption not triggered')
                except SystemExit:pass
            assert not target_bad.exists()
            pending=service.model_catalog();assert pending['pending'] and not pending['items'] and not target_bad.exists()
            try:service.initialize_execution('global','fixture.exe');raise AssertionError('partial model initialized')
            except ValueError as exc:assert '未完成' in str(exc)
            assert service.model_install_recover()['recovered'] and tx.digest(target_bad)==before
            checks.append('interrupted_directory_swap_blocks_initialization_and_catalog_cannot_recreate_missing_target_before_recovery')

            catalog=service.model_catalog();transfer.fail=0;transfer.data=pack(bad['required']);transfer.seen.clear();transfer.release.clear();transfer.block=True
            job=service.model_download_start(bad['id'],catalog['revision']);assert transfer.seen.wait(3)
            close=threading.Thread(target=service.close);close.start();time.sleep(.08);assert close.is_alive()
            transfer.release.set();transfer.block=False;close.join(10);assert not close.is_alive()
            result=service._downloads.status(job['job_id']);assert result['state']=='cancelled' and not result['busy']
            checks.append('service_close_cancels_and_waits_original_http_thread_without_game_input')
        finally:
            transfer.release.set();service.close()
    output=dict(checks=checks,count=len(checks),game_accessed=False,external_network_blocked=True,weights='non-executable synthetic file fixtures')
    (root/'result.json').write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
    (root/'catalog.json').write_text(json.dumps(initial_catalog,ensure_ascii=False),encoding='utf-8')
    print(json.dumps(output,ensure_ascii=False))


if __name__=='__main__':main()
