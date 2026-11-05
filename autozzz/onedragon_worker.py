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
        mcp=getattr(service,'_mcp_service',None)
        if mcp is not None:mcp.close()
        execution=getattr(service,'_execution',None)
        if execution is not None:
            try:execution.stop()
            finally:
                if execution.worker is not None:execution.worker.join()
    finally:service.close()


def verify_worker_identity(args):
    if args.runtime_identity is None:
        if args.runtime_identity_digest is not None or args.runtime_revision!=UPSTREAM_REVISION:
            raise ValueError('非固定SDK需要已验证的候选身份。')
        return UPSTREAM_REVISION
    from .runtime_selection import verify_identity
    result=verify_identity(args.runtime_identity,args.runtime_identity_digest,source=args.source,
        python=Path(sys.executable),bridge=Path(__file__).resolve().parent.parent,
        revision=args.runtime_revision,protocol=PROTOCOL_VERSION)
    return result['revision']


def add_runtime_identity_arguments(parser):
    parser.add_argument('--runtime-revision',default=UPSTREAM_REVISION)
    parser.add_argument('--runtime-identity',type=Path)
    parser.add_argument('--runtime-identity-digest')
    parser.add_argument('--stable-data',type=Path)
    parser.add_argument('--product-root',type=Path)
    parser.add_argument('--selection-generation')
    parser.add_argument('--management-only',action='store_true')


def validate_worker_request(service,method):
    service.runtime_guard.check_request(method)
    if service._rotation_sealed and method!='hello':
        raise RuntimeError('该轮换工作进程已收尾，请关闭原进程。')
    if service._rotation_guard is None:return
    allowed={'hello','rotation_select','rotation_checkpoint','rotation_release','rotation_control','rotation_window',
             'initialize_execution','prepare_application','start_application','prepare_login','start_login',
             'prepare_client_close','start_client_close','execution_status','pause_execution','stop_execution',
             'trace_configure','trace_snapshot','trace_capture','global_capture_status','global_capture_capture','game_launch_options'}
    if method not in allowed:raise RuntimeError('轮换期间不能编辑或重载其他配置。')
    if method not in {'hello','execution_status','pause_execution','stop_execution','rotation_release','trace_configure','trace_snapshot'}:
        service._rotation_guard.check()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--data", required=True, type=Path)
    add_runtime_identity_arguments(parser)
    args = parser.parse_args()
    output = sys.stdout
    sys.stdout = sys.stderr
    service = None
    run_lease = None
    try:
        if sys.version_info[:2] != (3, 11):
            raise RuntimeError("一条龙必须使用独立的 Python 3.11 环境。")
        actual_revision=verify_worker_identity(args)
        from .onedragon_service import METHODS, OneDragonService
        from .task_lease import JoinedRunLease
        run_lease=JoinedRunLease.from_environment()
        service = OneDragonService(args.source, args.data,run_lease=run_lease,
            runtime_revision=actual_revision,stable_data=args.stable_data,product_root=args.product_root,
            runtime_generation=args.selection_generation,management_only=args.management_only)
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
                validate_worker_request(service,method)
                if method == "hello":
                    result = dict(protocol=PROTOCOL_VERSION, revision=actual_revision,
                                  python=sys.version.split()[0], pid=os.getpid(), input_enabled=False,task_lease=run_lease is not None)
                elif method in METHODS:
                    if method=='mcp_stop':result=getattr(service,method)(**request['params'])
                    else:
                        with service._dispatch_lock:result = getattr(service, method)(**request["params"])
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
