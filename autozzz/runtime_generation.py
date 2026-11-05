"""Keep old SDK windows from writing or sending input after a runtime switch."""
from __future__ import annotations

import hashlib
from pathlib import Path
import re


def selection_generation(stable_data: Path) -> str:
    path = Path(stable_data) / 'autozzz' / 'sdk-selection.json'
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return '-'


class RuntimeGeneration:
    def __init__(self, stable_data: Path, generation: str | None, management_only: bool = False):
        if generation is not None and not (generation == '-' or re.fullmatch(r'[0-9a-f]{64}', generation)):
            raise ValueError('SDK 选择版本标识无效。')
        self.stable_data = Path(stable_data)
        self.generation = generation
        self.management_only = management_only

    def check_current(self):
        # The stable recovery connection must remain usable when a candidate fails.
        if self.management_only or self.generation is None:
            return
        if (self.stable_data/'autozzz/sdk-activation.json').exists():
            raise RuntimeError('SDK 切换存在待核对事务，请从 SDK 管理窗口恢复后重新连接。')
        if selection_generation(self.stable_data) != self.generation:
            raise RuntimeError('SDK 已切换，请关闭并重新打开原功能窗口后继续。')

    def check_execution(self):
        if self.management_only:
            raise RuntimeError('此连接仅用于 SDK 管理，请从任务页重新连接后运行。')
        self.check_current()

    def check_request(self, method: str):
        if method in {'hello', 'execution_status', 'pause_execution', 'stop_execution', 'mcp_stop',
                      'mcp_status', 'mcp_revoke', 'mcp_action_disable', 'mcp_edit_disable'}:
            return
        if self.management_only:
            if method in {'catalog', 'configuration', 'describe_configuration', 'policies', 'policy_validate',
                          'source_catalog', 'source_start', 'source_status', 'source_cancel',
                          'sdk_catalog', 'sdk_review', 'sdk_build', 'sdk_refresh', 'sdk_status', 'sdk_cancel', 'sdk_activate', 'sdk_rollback',
                          'sdk_activation_review', 'sdk_recover_activation', 'sdk_prepare_tool',
                          'program_catalog', 'program_check', 'program_download', 'program_import',
                          'program_restore', 'program_status', 'program_cancel',
                          'environment_tools_catalog', 'environment_tools_start', 'environment_tools_status',
                          'environment_tools_cancel', 'environment_tools_apply',
                          'global_capture_status', 'global_capture_capture'}:
                return
            raise RuntimeError('此连接仅用于 SDK 管理，请从对应功能页重新连接。')
        self.check_current()
