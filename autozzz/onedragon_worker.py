"""一条龙独立进程入口。标准输出只传协议，普通日志写入标准错误。"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from .onedragon_runtime import MAX_MESSAGE_BYTES, PROTOCOL_VERSION, UPSTREAM_REVISION


def close_service(service):
    try:
        execution=getattr(service,'_execution',None)
        if execution is not None:
            try:execution.stop()
            finally:
                if execution.worker is not None:execution.worker.join()
    finally:service.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--data", required=True, type=Path)
    args = parser.parse_args()
    output = sys.stdout
    sys.stdout = sys.stderr
    service = None
    run_lease = None
    try:
        if sys.version_info[:2] != (3, 11):
            raise RuntimeError("一条龙必须使用独立的 Python 3.11 环境。")
        from .onedragon_service import METHODS, OneDragonService
        from .task_lease import JoinedRunLease
        run_lease=JoinedRunLease.from_environment()
        service = OneDragonService(args.source, args.data,run_lease=run_lease)
        while True:
            line = sys.stdin.buffer.readline(MAX_MESSAGE_BYTES + 1)
            if not line:
                break
            if len(line) > MAX_MESSAGE_BYTES or not line.endswith(b"\n"):
                raise ValueError("请求过大或未完整结束。")
            request_id = None
            try:
                request = json.loads(line)
                if not isinstance(request, dict) or set(request) != {"id", "method", "params"}:
                    raise ValueError("请求结构无效。")
                request_id = request["id"]
                if not isinstance(request_id, str) or not isinstance(request["params"], dict):
                    raise ValueError("请求编号和参数无效。")
                method = request["method"]
                if service._rotation_sealed and method!='hello':raise RuntimeError('该轮换工作进程已收尾，请关闭原进程。')
                if service._rotation_guard is not None:
                    allowed={'hello','rotation_select','rotation_checkpoint','rotation_release','rotation_control','rotation_window','initialize_execution','prepare_application','start_application','prepare_login','start_login','prepare_client_close','start_client_close','execution_status','pause_execution','stop_execution','trace_configure','trace_snapshot'}
                    if method not in allowed:raise RuntimeError('轮换期间不能编辑或重载其他配置。')
                if method == "hello":
                    result = dict(protocol=PROTOCOL_VERSION, revision=UPSTREAM_REVISION,
                                  python=sys.version.split()[0], pid=os.getpid(), input_enabled=False,task_lease=run_lease is not None)
                elif method in METHODS:
                    if service._rotation_guard is not None and method not in ('execution_status','pause_execution','stop_execution','rotation_release','trace_configure','trace_snapshot'):
                        service._rotation_guard.check()
                    result = getattr(service, method)(**request["params"])
                else:
                    raise ValueError(f"未开放的一条龙方法：{method}")
                response = dict(id=request_id, result=result)
                encoded = json.dumps(response, ensure_ascii=False, allow_nan=False)
                if len(encoded.encode("utf-8")) > MAX_MESSAGE_BYTES - 1:
                    raise ValueError("响应过大。")
            except Exception as exc:
                encoded = json.dumps(dict(id=request_id, error=f"{type(exc).__name__}: {exc}"), ensure_ascii=False)
            output.write(encoded + "\n")
            output.flush()
    finally:
        try:
            if service:close_service(service)
        finally:
            if run_lease is not None:run_lease.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
