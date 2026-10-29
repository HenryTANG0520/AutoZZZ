"""实际环境配置和 QQ 网关协议回放；阻止真实网络、游戏及注册表写入。"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import io
import json
import logging
import platform
from pathlib import Path
import queue
import sys
import threading
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def wait(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline: raise AssertionError("环境/QQ 回放超时")
        time.sleep(.01)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    args = parser.parse_args()
    marker = args.data / ".autozzz-environment-binding-test"
    if args.data.exists() and any(args.data.iterdir()) and not marker.is_file(): raise SystemExit("请使用空目录或本工具拥有的测试目录。")
    args.data.mkdir(parents=True, exist_ok=True)
    marker.write_text("isolated SDK protocol test", encoding="utf-8")
    from autozzz.onedragon_runtime import RuntimePaths
    root = Path(__file__).resolve().parents[1]
    paths = RuntimePaths.discover(root)
    platform.uname()  # Windows 首次版本查询可能调用 ver；在禁止创建进程前缓存只读结果。
    RuntimePaths(paths.source, paths.python, args.data.resolve()).prepare()
    sys.path.insert(0, str(paths.source / "src"))
    from autozzz.onedragon_service import OneDragonService
    from autozzz.onedragon_environment import EnvironmentStore
    from autozzz.onedragon_notify import NotificationStore
    from one_dragon.base.push.channel import qq_bot as qq
    from one_dragon.utils.log_utils import log
    import requests
    import websocket
    capture, checks, calls, sockets = io.StringIO(), [], [], []
    handler = logging.StreamHandler(capture)
    def forbidden(*_, **__): raise AssertionError("离线测试禁止外部操作")
    with ExitStack() as blocks:
        for target in ("subprocess.Popen", "winreg.SetValueEx", "winreg.DeleteValue", "requests.sessions.Session.request",
                       "socket.socket.connect", "socket.socket.connect_ex", "socket.getaddrinfo"):
            blocks.enter_context(patch(target, side_effect=forbidden))
        service = OneDragonService(paths.source, args.data.resolve())
        ctx, env = service.ctx, EnvironmentStore(service)
        notify, push = NotificationStore(service), ctx.push_service
        log.addHandler(handler)
        try:
            def save_env(values):
                return env.save("_environment", values, env.describe("_environment")["revision"])
            initial = env.describe("_environment")
            proxy = "http://proxy-user:p%40ssword@127.0.0.1:17890"
            selected_source = next(f for f in initial["fields"] if f["name"] == "resource_source")["options"][-1]["value"]
            saved = save_env(dict(proxy_type="personal", personal_proxy=proxy, screenshot_method="pil", resource_source=selected_source, is_debug=False))
            assert proxy not in json.dumps(saved) and "proxy-user" not in json.dumps(saved)
            fresh = env.config("_environment")
            assert fresh.personal_proxy == proxy and fresh.screenshot_method == "pil" and fresh.get_resource_source_order()[0] == selected_source
            kept = save_env(dict(personal_proxy=""))
            assert env.config("_environment").personal_proxy == proxy
            before = Path(fresh.file_path).read_bytes()
            for values, revision in ((dict(clear_personal_proxy=True), kept["revision"]),
                                     (dict(personal_proxy="http://other.invalid", clear_personal_proxy=True), kept["revision"]),
                                     (dict(is_debug="false"), kept["revision"]),
                                     (dict(resource_source="unknown"), kept["revision"]),
                                     (dict(screenshot_method="auto"), initial["revision"])):
                try: env.save("_environment", values, revision); raise AssertionError("环境无效更新未拒绝")
                except ValueError: pass
                assert Path(fresh.file_path).read_bytes() == before
            save_env(dict(proxy_type="None", clear_personal_proxy=True, screenshot_method="auto"))
            assert env.config("_environment").personal_proxy == ""
            checks.append("actual_environment_atomic_save_proxy_redaction_keep_clear_validation_conflict_and_source_order")

            models = env.describe("_models")
            model_values = {f["name"]: f["options"][-1]["value"] if f["kind"] == "enum" else False for f in models["fields"]}
            saved_models = env.save("_models", model_values, models["revision"])
            actual_model = env.config("_models")
            assert all(getattr(actual_model, k) == v for k, v in model_values.items())
            raw = Path(actual_model.file_path).read_bytes()
            for changes in (dict(flash_classifier="../unknown"), dict(ocr="ppocrv5"), dict(ocr_use_gpu="false")):
                try: env.save("_models", changes, saved_models["revision"]); raise AssertionError("无效模型未拒绝")
                except ValueError: pass
                assert Path(actual_model.file_path).read_bytes() == raw
            checks.append("actual_recommended_backup_model_properties_and_typed_gpu_options")

            def save_qq(**values):
                description = notify.describe_channel("QQ_BOT")
                return notify.save_channel("QQ_BOT", values, description["revision"])
            save_qq(enabled=False, APP_ID="fixture-app-id", CLIENT_SECRET="fixture-client-secret", clear_OPENID=True, clear_GROUP_OPENID=True)
            # 每次工具运行恢复关闭，避免上次夹具的配置影响本轮结果。
            settings = notify.describe_channel("_settings")
            notify.save_channel("_settings", dict(proxy="NONE", send_image=False), settings["revision"])
            description = notify.describe_channel("QQ_BOT")
            assert description["binding_supported"] and description["binding_ready"] and not description["valid"]
            assert not ctx.notify_config.enable_notify and not push.enabled_channels()

            class Response:
                def __init__(self, data): self.data, self.status_code, self.text = data, 200, json.dumps(data)
                def json(self): return self.data
            scenario = dict(events=[], post_gate=None, retry=False, close_gate=None, timeout=False)
            post_entered, retry_entered = threading.Event(), threading.Event()
            def post(url, **kwargs):
                assert url == qq.QQBot._TOKEN_URL, "禁止消息发送"
                assert kwargs["json"] == dict(appId="fixture-app-id", clientSecret="fixture-client-secret")
                calls.append(("post", kwargs))
                post_entered.set()
                if scenario["post_gate"] is not None: scenario["post_gate"].wait(5)
                if scenario["retry"]:
                    retry_entered.set()
                    raise requests.ConnectionError("fixture-client-secret " + ("proxy-user p@ssword " if kwargs["proxies"]["https"] else "") + "failure")
                return Response(dict(access_token="fixture-access-token", expires_in=7200))
            def get(url, **kwargs):
                if url == "https://other-thread.invalid":
                    assert kwargs == dict(timeout=3, proxies={"https": "outside-context"})
                    return Response(dict(other_thread=True))
                assert url == qq._GATEWAY_URL
                assert kwargs["headers"]["Authorization"] == "QQBot fixture-access-token"
                calls.append(("get", kwargs))
                return Response(dict(url="wss://qq-gateway.invalid/fixture-access-token"))
            class Socket:
                def __init__(self):
                    self.events = queue.Queue()
                    for event in scenario["events"]: self.events.put(event)
                    self.aborted, self.closed, self.sent, self.close_gate = threading.Event(), False, [], scenario["close_gate"]
                    self.recv_entered = threading.Event()
                def recv(self):
                    self.recv_entered.set()
                    try: event = self.events.get(timeout=.03)
                    except queue.Empty:
                        if self.aborted.is_set(): raise websocket.WebSocketConnectionClosedException()
                        raise websocket.WebSocketTimeoutException()
                    if isinstance(event, threading.Event):
                        deadline = time.monotonic() + 5
                        while not event.wait(.01) and not self.aborted.is_set() and time.monotonic() < deadline: pass
                        if self.aborted.is_set(): raise websocket.WebSocketConnectionClosedException()
                        raise websocket.WebSocketTimeoutException()
                    return json.dumps(event) if not isinstance(event, str) else event
                def send(self, value): self.sent.append(json.loads(value))
                def settimeout(self, value): assert 0 < value <= 60
                def abort(self): self.aborted.set()
                def close(self):
                    if self.close_gate is not None: self.close_gate.wait(5)
                    self.closed = True
            def connect(url, **kwargs):
                assert url == "wss://qq-gateway.invalid/fixture-access-token"
                calls.append(("ws", kwargs))
                ws = Socket(); sockets.append(ws)
                if scenario["timeout"]: service._binding.transport.deadline = time.monotonic() - .01
                return ws
            blocks.enter_context(patch.object(requests, "post", side_effect=post))
            blocks.enter_context(patch.object(requests, "get", side_effect=get))
            blocks.enter_context(patch.object(websocket, "create_connection", side_effect=connect))
            blocks.enter_context(patch.object(qq, "_generate_verify_code", return_value="2468"))
            hello, ready = dict(op=10, d=dict(heartbeat_interval=5000)), dict(op=0, t="READY", s=1, d={})
            def message(kind, **data): return dict(op=0, t=kind, s=2, d=data)
            def start(events, group=False, **extras):
                calls.clear();sockets.clear();post_entered.clear();retry_entered.clear()
                scenario.update(events=events, post_gate=None, retry=False, close_gate=None, timeout=False)
                scenario.update(extras)
                return service.qq_bind_start(group, notify.describe_channel("QQ_BOT")["revision"])
            def done(job):
                wait(lambda: not service.qq_bind_status(job["job_id"])["busy"])
                return service.qq_bind_status(job["job_id"])
            def assert_private(result):
                text = json.dumps(result) + capture.getvalue()
                for value in ("fixture-client-secret", "fixture-access-token", "fixture-recipient", "proxy-user", "p@ssword"):
                    assert value not in text, value
            original_dependencies = (qq.requests, qq.websocket, qq.time)

            gate = threading.Event()
            job = start([hello, ready, "{malformed", message("C2C_MESSAGE_CREATE", author=dict(user_openid="wrong-user"), content="wrong code"), gate,
                         message("C2C_MESSAGE_CREATE", author=dict(user_openid="fixture-recipient-user"), content="2468")])
            wait(lambda: service.qq_bind_status(job["job_id"])["state"] == "waiting")
            current = service.qq_bind_status(job["job_id"])
            assert current["code"] == "2468" and current["busy"]
            other_results = []
            def outside_request():
                qq.time.sleep(.001)
                other_results.append(qq.requests.get("https://other-thread.invalid", timeout=3, proxies={"https": "outside-context"}).json())
            other = threading.Thread(target=outside_request)
            other.start();other.join(3)
            assert not other.is_alive() and other_results == [dict(other_thread=True)]
            for action in (lambda: service.qq_bind_start(False, "unused"), lambda: notify.describe_channel("QQ_BOT")):
                try: action(); raise AssertionError("活动绑定中重入")
                except RuntimeError: pass
            gate.set();result = done(job)
            assert result["state"] == "success" and result["saved"]
            assert push.get_channel_config("QQ_BOT")["OPENID"] == "fixture-recipient-user"
            assert all(kwargs["proxies"] == dict(http="", https="", all="") for method, kwargs in calls if method in ("post", "get"))
            assert next(kwargs for method, kwargs in calls if method == "ws")["http_no_proxy"] == ["*"]
            assert not push.enabled_channels() and not ctx.notify_config.enable_notify
            assert service.qq_bind_cancel(job["job_id"])["saved"]
            assert_private(result)
            checks.append("actual_single_chat_gateway_hello_identify_malformed_wrong_code_match_and_private_atomic_save")
            checks.append("binding_dependency_scope_preserves_other_threads_proxy_and_request_parameters")

            gate = threading.Event()
            job = start([hello, ready, message("GROUP_AT_MESSAGE_CREATE", group_openid="wrong-group", content="wrong"), gate,
                         message("GROUP_AT_MESSAGE_CREATE", group_openid="fixture-recipient-group", content="<@bot> 2468")], group=True)
            wait(lambda: "不匹配" in service.qq_bind_status(job["job_id"])["detail"])
            assert service.qq_bind_status(job["job_id"])["code"] == "2468"
            gate.set();result = done(job)
            assert result["state"] == "success" and push.get_channel_config("QQ_BOT")["GROUP_OPENID"] == "fixture-recipient-group"
            assert_private(result)
            checks.append("actual_group_ready_wrong_match_status_and_group_recipient_preserves_single_chat")

            for group, event in ((False, message("FRIEND_ADD", openid="fixture-recipient-friend")),
                                 (True, message("GROUP_ADD_ROBOT", group_openid="fixture-recipient-added-group"))):
                assert done(start([hello, ready, event], group=group))["state"] == "success"
            checks.append("actual_friend_and_group_add_event_bindings")

            before = Path(push.push_config.file_path).read_bytes()
            close_gate = threading.Event()
            job = start([hello, ready], close_gate=close_gate)
            wait(lambda: service.qq_bind_status(job["job_id"])["state"] == "waiting")
            result = service.qq_bind_cancel(job["job_id"])
            assert result["busy"] and result["state"] == "cancelling"
            close_gate.set();result = done(job)
            assert result["state"] == "cancelled" and not result["saved"] and sockets[0].closed
            assert Path(push.push_config.file_path).read_bytes() == before
            checks.append("cancel_aborts_receive_and_waits_original_socket_cleanup_without_saving")

            for events, group in (([threading.Event()], False), ([hello, threading.Event()], True)):
                job = start(events, group=group)
                wait(lambda: bool(sockets) and sockets[0].recv_entered.is_set())
                assert service.qq_bind_status(job["job_id"])["code"] == ""
                service.qq_bind_cancel(job["job_id"])
                assert done(job)["state"] == "cancelled" and sockets[0].closed
                assert Path(push.push_config.file_path).read_bytes() == before
            checks.append("cancel_initial_hello_and_group_ready_before_code_without_writing")

            original_init = qq._GatewaySession.__init__
            def overdue_heartbeat(session, *a, **k):
                original_init(session, *a, **k)
                session._next_heartbeat = time.time() - 1
            with patch.object(qq._GatewaySession, "__init__", overdue_heartbeat):
                job = start([hello, ready, message("FRIEND_ADD", openid="fixture-recipient-heartbeat")])
                assert done(job)["saved"] and any(packet["op"] == 1 for packet in sockets[0].sent)
            before = Path(push.push_config.file_path).read_bytes()
            checks.append("actual_gateway_heartbeat_retained_during_binding")

            post_gate = threading.Event()
            job = start([], post_gate=post_gate)
            assert post_entered.wait(5)
            assert service.qq_bind_cancel(job["job_id"])["busy"]
            assert [method for method, _ in calls] == ["post"]
            post_gate.set();assert done(job)["state"] == "cancelled"
            assert [method for method, _ in calls] == ["post"] and Path(push.push_config.file_path).read_bytes() == before
            checks.append("cancel_during_http_wait_never_starts_gateway_or_replacement_job")

            job = start([], retry=True)
            assert retry_entered.wait(5)
            service.qq_bind_cancel(job["job_id"])
            assert done(job)["state"] == "cancelled" and len(calls) == 1
            assert_private(service.qq_bind_status(job["job_id"]))
            checks.append("cancel_interrupts_actual_token_retry_backoff_and_redacts_exception")

            job = start([hello], timeout=True)
            result = done(job)
            assert result["state"] == "timeout" and sockets[0].closed and not result["saved"]
            assert Path(push.push_config.file_path).read_bytes() == before
            checks.append("deadline_closes_handshake_without_publishing_recipient")

            job = start([hello, dict(op=9, d={})])
            assert done(job)["state"] == "failed"
            assert len([1 for method, _ in calls if method == "post"]) == 1 and Path(push.push_config.file_path).read_bytes() == before
            checks.append("actual_authentication_rejection_propagates_without_auto_rebinding")

            gate = threading.Event()
            job = start([hello, ready, gate, message("C2C_MESSAGE_CREATE", author=dict(user_openid="fixture-recipient-new"), content="2468")])
            wait(lambda: service.qq_bind_status(job["job_id"])["state"] == "waiting")
            target = Path(push.push_config.file_path)
            changed = target.read_bytes() + b"\n# external writer\n"
            target.write_bytes(changed)
            gate.set();result = done(job)
            assert result["state"] == "failed" and "变化" in result["detail"] or result["state"] == "failed" and "修改" in result["detail"]
            assert target.read_bytes() == changed and not result["saved"]
            checks.append("external_revision_conflict_preserves_other_writer_without_overwriting_receiver")

            save_env(dict(proxy_type="personal", personal_proxy=proxy))
            settings = notify.describe_channel("_settings")
            notify.save_channel("_settings", dict(proxy="PERSONAL"), settings["revision"])
            result = done(start([hello, ready, message("FRIEND_ADD", openid="fixture-recipient-proxied")]))
            assert result["saved"]
            assert all(kwargs["proxies"]["https"] == proxy for method, kwargs in calls if method in ("post", "get"))
            ws_options = next(kwargs for method, kwargs in calls if method == "ws")
            assert ws_options["http_proxy_host"] == "127.0.0.1" and ws_options["http_proxy_port"] == 17890
            assert ws_options["http_proxy_auth"] == ("proxy-user", "p@ssword") and ws_options["http_no_proxy"] == [""]
            assert_private(result)
            checks.append("personal_proxy_applies_to_http_and_websocket_with_decoded_authentication")

            job = start([], retry=True)
            assert retry_entered.wait(5)
            service.qq_bind_cancel(job["job_id"])
            assert done(job)["state"] == "cancelled"
            assert_private(service.qq_bind_status(job["job_id"]))
            checks.append("personal_proxy_userinfo_redacted_from_actual_retry_logs")

            for address, reason in (("https://127.0.0.1:17890", "HTTPS"), ("socks5h://127.0.0.1:17890", "SOCKS")):
                save_env(dict(personal_proxy=address))
                calls.clear()
                with patch("autozzz.onedragon_qq_binding.importlib.util.find_spec", return_value=None):
                    try:
                        service.qq_bind_start(False, notify.describe_channel("QQ_BOT")["revision"])
                        raise AssertionError("不受支持的代理仍尝试 QQ 请求")
                    except ValueError as exc: assert reason in str(exc)
                assert not calls and not service._binding.busy
            save_env(dict(personal_proxy=proxy))
            checks.append("unsupported_proxy_or_missing_socks_dependencies_rejected_before_network")

            assert (qq.requests, qq.websocket, qq.time) == original_dependencies
            try: service.qq_bind_status("not-original-job"); raise AssertionError("未知绑定编号未拒绝")
            except ValueError: pass
            job = start([hello, ready])
            wait(lambda: service.qq_bind_status(job["job_id"])["state"] == "waiting")
            service._binding.close()
            assert not service._binding.busy and service.qq_bind_status(job["job_id"])["state"] == "cancelled"
            assert (qq.requests, qq.websocket, qq.time) == original_dependencies
            assert_private(service.qq_bind_status(job["job_id"]))
            checks.append("close_waits_original_binding_worker_and_restores_upstream_dependency_references")
            # 供原生界面布局回放读取的描述均脱敏。
            descriptions = dict(environment=env.describe("_environment"), models=env.describe("_models"), qq=notify.describe_channel("QQ_BOT"))
            (args.data / "ui-descriptions.json").write_text(json.dumps(descriptions, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(dict(checks=checks, count=len(checks), external_messages_sent=0, game_input_sent=False), ensure_ascii=False))
        finally:
            service.close()
            log.removeHandler(handler)


if __name__ == "__main__": main()
