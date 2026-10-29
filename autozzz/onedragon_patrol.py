"""让实际上游巡逻节点按已保存的路线组顺序执行。"""
from __future__ import annotations

import copy
from functools import wraps

from .onedragon_library_rules import route_selection


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
    def __init__(self, ctx): self.ctx, self.original = ctx, None

    def install(self):
        if self.original is not None: return
        from zzz_od.application.world_patrol.world_patrol_app import WorldPatrolApp
        original = self.original = WorldPatrolApp.init_world_patrol
        @wraps(original)
        def initialize(operation):
            if operation.ctx is not self.ctx: return original(operation)
            # 初始化节点可能在同一实例恢复或重新执行，不能累加上一次的路线。
            operation.route_list.clear()
            operation.route_idx = 0
            result = original(operation)
            if operation.config.route_list:
                group = next((g for g in self.ctx.world_patrol_service.get_world_patrol_route_lists() if g.name == operation.config.route_list), None)
                if group is None: return operation.round_fail("路线组不存在，请重新准备。")
                try: operation.route_list = route_selection(self.ctx.world_patrol_service.get_world_patrol_routes(), group)
                except ValueError as exc: return operation.round_fail(str(exc))
            return result
        annotation = copy.copy(original.operation_node_annotation)
        annotation.op_method = initialize
        initialize.operation_node_annotation = annotation
        WorldPatrolApp.init_world_patrol = initialize

    def close(self):
        if self.original is not None:
            from zzz_od.application.world_patrol.world_patrol_app import WorldPatrolApp
            WorldPatrolApp.init_world_patrol, self.original = self.original, None
