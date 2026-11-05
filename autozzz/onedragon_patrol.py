"""保留上游巡逻流程，按保存的顺序执行并记录逐路线结果。"""
from __future__ import annotations

from collections import Counter, deque
import copy
from functools import wraps

from .onedragon_library_rules import route_selection


MAX_ROUTE_RESULTS = 512
PATROL_INCOMPLETE_ERROR = "巡逻未完成，有路线失败或被跳过。"
RESTART_SKIP_STATUSES = (
    "界面消失已重开游戏并跳过路线 ",
    "界面消失重试耗尽，已重开游戏并跳过路线 ",
)


def patrol_incomplete(result: object) -> bool:
    return isinstance(result, dict) and result.get("outcome") == "incomplete"


def managed_patrol_service(ctx):
    from pathlib import Path
    from one_dragon.utils import os_utils
    from one_dragon.utils.log_utils import log
    from zzz_od.application.world_patrol.world_patrol_service import WorldPatrolService
    class ManagedPatrolService(WorldPatrolService):
        def get_world_patrol_route_lists(self):
            from .onedragon_library import read_route_groups
            groups, _, issues = read_route_groups(Path(os_utils.get_work_dir()))
            if issues: log.warning("路线组读取失败：%s", "、".join(row["file"] for row in issues))
            return groups
    return ManagedPatrolService(ctx)


class PatrolOrderControl:
    def __init__(self, ctx):
        self.ctx = ctx
        self.originals = []
        self.operation = None
        self.report = None
        self.routes = deque(maxlen=MAX_ROUTE_RESULTS)
        self.counts = Counter()
        self.progress_before = None

    def reset(self):
        self.report = None

    def status(self):
        return copy.deepcopy(self.report)

    @staticmethod
    def _progress(record):
        finished = record.finished
        route_ids = list(finished) if isinstance(finished, list) and all(isinstance(key, str) for key in finished) else None
        rounds = record.completed_rounds
        return dict(date=str(record.dt), completed_rounds=rounds if type(rounds) is int else None, finished=route_ids)

    @staticmethod
    def _route_names(routes):
        names = {}
        for route in routes:
            area = getattr(getattr(route, "tp_area", None), "full_name", "")
            anchor, number = getattr(route, "tp_name", ""), getattr(route, "idx", None)
            if area and anchor and type(number) is int:
                names[route.full_id] = f"{area} · {anchor} #{number}"
        return names

    def _execute(self, operation, original):
        self.operation = operation
        self.routes.clear()
        self.counts.clear()
        self.progress_before = None
        result = None
        try:
            result = original(operation)
            return result
        finally:
            self.operation = None
            status = None if result is None else result.status
            if result is None:
                outcome = "unverified"
            elif not result.success:
                outcome = "stopped" if status == "人工结束" else "failed"
            elif self.counts["failed"] or self.counts["skipped"]:
                outcome = "incomplete"
            else:
                outcome = "completed" if self.counts["completed"] else "no_work"
            record = operation.run_record
            self.report = dict(
                outcome=outcome, sdk_success=None if result is None else result.success,
                sdk_status=status, selected_route_ids=[route.full_id for route in operation.route_list],
                route_names=self._route_names(operation.route_list),
                round_limit=record.total_rounds, progress_before=self.progress_before,
                progress_after=self._progress(record), routes=list(self.routes),
                route_counts=dict(self.counts), omitted_routes=sum(self.counts.values()) - len(self.routes),
            )

    def _initialize(self, operation, original):
        if self.operation is operation:
            # 应用初始化已完成日期换代，记录实际用于续跑的进度。
            self.progress_before = self._progress(operation.run_record)
        # 初始化节点可能在同一实例恢复或重新执行，不能累加上一次的路线。
        operation.route_list.clear()
        operation.route_idx = 0
        result = original(operation)
        if operation.config.route_list:
            group = next((g for g in self.ctx.world_patrol_service.get_world_patrol_route_lists()
                          if g.name == operation.config.route_list), None)
            if group is None:
                return operation.round_fail("路线组不存在，请重新准备。")
            try:
                operation.route_list = route_selection(self.ctx.world_patrol_service.get_world_patrol_routes(), group)
            except ValueError as exc:
                return operation.round_fail(str(exc))
        return result

    def _run_route(self, operation, original):
        record = operation.run_record
        index, round_number = operation.route_idx, record.current_round
        if (self.operation is not operation or round_number > record.total_rounds
                or not 0 <= index < len(operation.route_list)):
            return original(operation)
        route_id = operation.route_list[index].full_id
        was_finished = route_id in record.finished
        result = original(operation)
        if operation.route_idx == index and not result.is_fail:
            return result
        status = str(result.status or "")
        if was_finished:
            outcome = "already_finished"
        elif route_id in record.finished:
            outcome = "completed"
        elif not result.is_fail and status.startswith(RESTART_SKIP_STATUSES):
            outcome = "skipped"
        else:
            outcome = "failed"
        self.routes.append(dict(round=round_number, route_id=route_id, outcome=outcome, status=status[:512]))
        self.counts[outcome] += 1
        return result

    def _patch(self, cls, name, handler):
        original = getattr(cls, name)

        @wraps(original)
        def wrapped(operation):
            if operation.ctx is not self.ctx:
                return original(operation)
            return handler(operation, original)

        annotation = getattr(original, "operation_node_annotation", None)
        if annotation is not None:
            wrapped.operation_node_annotation = copy.copy(annotation)
            wrapped.operation_node_annotation.op_method = wrapped
        self.originals.append((cls, name, cls.__dict__.get(name)))
        setattr(cls, name, wrapped)

    def install(self):
        if self.originals:
            return
        from zzz_od.application.world_patrol.world_patrol_app import WorldPatrolApp
        self._patch(WorldPatrolApp, "execute", self._execute)
        self._patch(WorldPatrolApp, "init_world_patrol", self._initialize)
        self._patch(WorldPatrolApp, "run_route", self._run_route)

        local_completion = WorldPatrolApp.__dict__.get("after_operation_done")

        @wraps(WorldPatrolApp.after_operation_done)
        def settle(operation, result):
            if (operation is self.operation and result.success
                    and (self.counts["failed"] or self.counts["skipped"])):
                # 保留 SDK 原返回供逐路线报告使用，按失败写记录并发送结束通知。
                result = operation.op_fail(PATROL_INCOMPLETE_ERROR, result.data)
            if local_completion is not None:
                return local_completion(operation, result)
            # 应用组会在运行时安装父类的结算回调。
            return super(WorldPatrolApp, operation).after_operation_done(result)

        self.originals.append((WorldPatrolApp, "after_operation_done", local_completion))
        WorldPatrolApp.after_operation_done = settle

    def close(self):
        for cls, name, original in reversed(self.originals):
            if original is None:
                delattr(cls, name)
            else:
                setattr(cls, name, original)
        self.originals.clear()
