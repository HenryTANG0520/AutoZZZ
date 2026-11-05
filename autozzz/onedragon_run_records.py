"""Read and explicitly reset three account-local OneDragon application records."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import os
from pathlib import Path
import uuid

from .config_transaction import atomic_apply
from .run_lock import RunLock, profile_lock


RUN_RECORD_APPS = ('world_patrol', 'lost_void', 'withered_domain')
MAX_RECORD_BYTES = 1024 * 1024


def _mapping(raw):
    from one_dragon.utils.yaml_utils import safe_load

    if raw is None:
        return {}
    try:
        value = safe_load(raw.decode('utf-8'))
    except Exception as exc:
        raise ValueError('运行记录或关联配置的 YAML 无法读取。') from exc
    if value is not None and not isinstance(value, dict):
        raise ValueError('运行记录或关联配置须为 YAML 映射。')
    return value if value is not None else {}


def _number(value):
    if type(value) is not int or value < 0:
        raise ValueError('运行记录中的次数或路线数量无效。')
    return value


def _flag(value):
    if type(value) is not bool:
        raise ValueError('运行记录中的完成标记无效。')
    return value


def _revision(sources, current_date):
    digest = sha256(current_date.encode('ascii'))
    for path, raw in sources.items():
        digest.update(str(path).casefold().encode('utf-8') + b'\0')
        digest.update(b'\0' if raw is None else b'\1' + sha256(raw).digest())
    return digest.hexdigest()


def _read_only_config(cls, raw):
    # Preserve source defaults and getters without running ApplicationConfig's
    # legacy copy-on-read migration merely to display the current counters.
    from one_dragon.base.config.yaml_operator import YamlOperator

    config = cls.__new__(cls)
    YamlOperator.__init__(config)
    config.data = _mapping(raw)
    return config


class RunRecordStore:
    def __init__(self, service):
        self.service = service
        self.root = Path(service.data).resolve()

    @contextmanager
    def _scope(self, write=False):
        self.service._assert_idle()
        if write:
            self.service.runtime_guard.check_execution()
        # Match SDK upgrade's lock order, and exclude both the original daily
        # runner and native workers that may be writing their run records.
        with RunLock():
            ctx = self.service.ctx
            with profile_lock(self.root):
                self.service._assert_idle()
                if write:
                    self.service.runtime_guard.check_execution()
                yield ctx

    def _checked(self, path):
        if not path.is_relative_to(self.root) or path.resolve() != path:
            raise ValueError('运行记录路径不在本账号资料目录，或被重定向。')
        return path

    def _read(self, path):
        self._checked(path)
        try:
            with path.open('rb') as stream:
                raw = stream.read(MAX_RECORD_BYTES + 1)
        except FileNotFoundError:
            return None
        if len(raw) > MAX_RECORD_BYTES:
            raise ValueError('运行记录或关联配置超过 1 MiB。')
        return raw

    def _load(self, ctx, app_id):
        from one_dragon.base.config.game_account_config import GameAccountConfig
        from one_dragon.base.config.one_dragon_config import OneDragonConfig
        from one_dragon.base.config.yaml_operator import invalidate_cache
        from one_dragon.base.operation.application.application_const import DEFAULT_GROUP_ID
        from one_dragon.utils import os_utils

        if not isinstance(app_id, str) or app_id not in RUN_RECORD_APPS:
            raise ValueError('此应用不支持查看或重置运行记录。')
        if Path(os_utils.get_work_dir()).resolve() != self.root:
            raise ValueError('运行记录与当前 SDK 资料目录不一致。')

        registry_path = self.root / 'config/one_dragon.yml'
        registry_raw = self._read(registry_path)
        registry = _read_only_config(OneDragonConfig, registry_raw)
        registry.instance_list = []
        try:
            registry._init_instance_list()
        except (TypeError, ValueError) as exc:
            raise ValueError('无法识别当前账号。') from exc
        active = [instance for instance in registry.instance_list if instance.active is True]
        if (len(active) != 1 or type(active[0].idx) is not int or active[0].idx < 1
                or active[0].idx != ctx.current_instance_idx
                or sum(instance.idx == active[0].idx for instance in registry.instance_list) != 1):
            raise ValueError('当前账号已变化或无效，请重新连接。')
        instance = active[0]
        account_dir = self.root / 'config' / f'{instance.idx:02d}'
        game_config_path = account_dir / 'game_account.yml'
        app_config_path = account_dir / DEFAULT_GROUP_ID / f'{app_id}.yml'
        old_config_path = account_dir / f'{app_id}.yml'
        record_path = account_dir / 'app_run_record' / f'{app_id}.yml'
        sources = {registry_path: registry_raw}
        sources.update({path: self._read(path) for path in (game_config_path, app_config_path, old_config_path, record_path)})
        offset = _read_only_config(GameAccountConfig, sources[game_config_path]).game_refresh_hour_offset
        config_raw = sources[app_config_path] if sources[app_config_path] is not None else sources[old_config_path]
        record_data = _mapping(sources[record_path])
        # Source YAML caches key by mtime; a fresh object must see these bytes.
        invalidate_cache(str(record_path))
        if app_id == 'world_patrol':
            from zzz_od.application.world_patrol.world_patrol_config import WorldPatrolConfig
            from zzz_od.application.world_patrol.world_patrol_run_record import WorldPatrolRunRecord

            config = _read_only_config(WorldPatrolConfig, config_raw)
            record = WorldPatrolRunRecord(instance.idx, offset)
        elif app_id == 'lost_void':
            from zzz_od.application.hollow_zero.lost_void.lost_void_config import LostVoidConfig
            from zzz_od.application.hollow_zero.lost_void.lost_void_run_record import LostVoidRunRecord

            config = _read_only_config(LostVoidConfig, config_raw)
            record = LostVoidRunRecord(config, instance.idx, offset)
        else:
            from zzz_od.application.hollow_zero.withered_domain.withered_domain_config import WitheredDomainConfig
            from zzz_od.application.hollow_zero.withered_domain.withered_domain_run_record import WitheredDomainRunRecord

            config = _read_only_config(WitheredDomainConfig, config_raw)
            record = WitheredDomainRunRecord(config, instance.idx, offset)
        if (Path(record.file_path) != record_path or Path(record._get_write_path()) != record_path
                or record.data != record_data or not self._matches(sources)):
            raise ValueError('运行记录路径或内容已变化，请重新读取。')
        return instance, config, record, record_path, sources

    def _matches(self, sources):
        return all(self._read(path) == raw for path, raw in sources.items())

    @staticmethod
    def _describe(instance, config, record, exists):
        from one_dragon.utils.os_utils import get_sunday_dt

        current_date = record.get_current_dt()
        try:
            same_day = record.dt == current_date
            same_week = get_sunday_dt(record.dt) == get_sunday_dt(current_date)
            status = ('waiting', 'success', 'failed', 'running')[_number(record.run_status_under_now)]
        except (IndexError, TypeError, ValueError) as exc:
            raise ValueError('运行记录中的日期或状态无效。') from exc
        if record.app_id == 'world_patrol':
            scope = 'daily'
            total = _number(config.daily_loop_count)
            routes = _number(record.routes_per_round)
            rounds = _number(record.completed_rounds)
            if not isinstance(record.finished, list) or not all(isinstance(item, str) for item in record.finished):
                raise ValueError('运行记录中的已完成路线列表无效。')
            rounds, finished = (rounds, len(record.finished)) if same_day else (0, 0)
            fraction = (finished % routes) / routes if routes > 0 else 0
            progress = dict(completed_rounds=rounds, finished_routes=finished, routes_per_round=routes,
                            daily_plan_rounds=total, daily_progress=min(rounds + fraction, total))
        else:
            scope = 'weekly'
            daily = _number(record.daily_run_times)
            weekly = _number(record.weekly_run_times)
            progress = dict(daily_run_times=daily if same_day else 0, weekly_run_times=weekly if same_week else 0,
                            daily_plan_times=_number(config.daily_plan_times), weekly_plan_times=_number(config.weekly_plan_times))
            fields = ('bounty_commission_complete', 'eval_point_complete', 'complete_task_force_with_up') if record.app_id == 'lost_void' else ('no_eval_point',)
            progress.update({field: _flag(getattr(record, field)) and same_week for field in (*fields, 'period_reward_complete')})
        return dict(app_id=record.app_id, instance_idx=instance.idx, instance_name=instance.name, exists=exists,
                    scope=scope, record_date=record.get('dt', ''), current_date=current_date,
                    status=status, progress=progress)

    def describe(self, app_id):
        with self._scope() as ctx:
            instance, config, record, path, sources = self._load(ctx, app_id)
            result = self._describe(instance, config, record, sources[path] is not None)
            return dict(result, revision=_revision(sources, result['current_date']))

    def _archive(self, path, raw):
        name = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_') + uuid.uuid4().hex + '.yml'
        archive = self._checked(self.root / 'autozzz/run-record-archives' / path.parent.parent.name / path.stem / name)
        archive.parent.mkdir(parents=True, exist_ok=True)
        self._checked(archive)
        with archive.open('xb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        return str(archive)

    def reset(self, app_id, revision, instance_idx):
        if type(instance_idx) is not int:
            raise ValueError('重置运行记录需要当前账号编号。')
        with self._scope(write=True) as ctx:
            instance, config, record, path, sources = self._load(ctx, app_id)
            raw = sources[path]
            result = self._describe(instance, config, record, raw is not None)
            today = result['current_date']
            if instance.idx != instance_idx or revision != _revision(sources, today):
                raise ValueError('账号、日期或运行记录已变化，请重新读取后确认。')
            if raw is None:
                return dict(result, revision=revision, archive=None)

            def reset_source():
                if app_id != 'withered_domain':
                    record.reset_record()
                if app_id != 'world_patrol':
                    record.reset_for_weekly()

            archive = None
            def apply():
                nonlocal archive, result
                reset_source()
                result = self._describe(instance, config, record, True)
                archive = self._archive(path, raw)

            def verify():
                self.service.runtime_guard.check_execution()
                return record.get_current_dt() == today and self._matches(sources)

            file_revision = sha256(str(path).casefold().encode('utf-8') + b'\0' + raw).hexdigest()
            atomic_apply(record, file_revision, apply, self.root, guard=verify)
            from one_dragon.base.config.yaml_operator import invalidate_cache

            invalidate_cache(str(path))
            ctx.run_context.clear_application_cache()
            sources[path] = self._read(path)
            return dict(result, revision=_revision(sources, today), archive=archive)
