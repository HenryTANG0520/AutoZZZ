"""Publish both stable SDK selections behind gates recognized by the old runtime."""
from contextlib import ExitStack
from hashlib import sha256
import json
from pathlib import Path

from .onedragon_bundle import digest, filesystem_path
from .onedragon_runtime import RuntimePaths
from .program_package import verify_package
from .program_sdk_binding import PROFILES, verify_sdk_transition
from .run_lock import profile_lock
from .runtime_selection import plain, read_json, selection_path, snapshot_path, tree_hash
from .sdk_stable_migration import sha256_token
from .sdk_upgrade_files import atomic_bytes, data_state


def _payload(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode('utf-8')


def _read(path, limit):
    path = filesystem_path(plain(path))
    if not path.exists():
        return None
    with path.open('rb') as stream:
        raw = stream.read(limit+1)
    if len(raw) > limit:
        raise ValueError('SDK 选择或更新门槛超出大小限制。')
    return raw


def _marker_hash(root):
    marker = plain(root/'autozzz-runtime.json')
    return digest(marker) if filesystem_path(marker).is_file() else None


def _roots(tx, binding):
    controls, sources = {}, {}
    for profile in PROFILES:
        row = binding['profiles'][profile]
        control = plain(RuntimePaths.discover(tx.target, profile, use_selection=False).data.resolve())
        source = plain(Path(row['source']))
        if str(control) != row['control'] or source != control and source != snapshot_path(control, source.name):
            raise ValueError('程序更新的区服控制目录或有效快照已变化。')
        controls[profile], sources[profile] = control, source
    if len(set(controls.values())) != len(PROFILES):
        raise ValueError('两个区服共用控制目录，无法联合提交。')
    return controls, sources


def _locks(roots):
    stack = ExitStack()
    try:
        for root in sorted(set(roots), key=str):
            stack.enter_context(profile_lock(root))
    except BaseException:
        stack.close()
        raise
    return stack


class ProgramSdkCommit:
    """The caller holds the update task lock and provides preflighted snapshots."""

    @classmethod
    def prepare(cls, tx, snapshots):
        if tx.data['state'] != 'prepared' or 'sdk_commit' in tx.data:
            raise ValueError('程序更新已进入联合提交。')
        binding = verify_sdk_transition(tx)
        controls, sources = _roots(tx, binding)
        stable = {profile for profile, row in binding['profiles'].items() if row['kind'] == 'stable'}
        if set(snapshots) != stable:
            raise ValueError('必须提供每个稳定区服已预检的新快照。')
        targets = {p: snapshot_path(controls[p], value['data_id']) for p, value in snapshots.items()}
        with _locks([*controls.values(), *sources.values(), *targets.values()]):
            rows = {}
            for profile in PROFILES:
                control, source = controls[profile], sources[profile]
                before = _read(selection_path(control), 8192)
                old = binding['profiles'][profile]
                if ('-' if before is None else sha256(before).hexdigest()) != old['selector']:
                    raise ValueError('原区服 SDK 选择在准备后变化。')
                if _marker_hash(source) != old['marker'] or tree_hash(data_state(source)) != old['data']:
                    raise ValueError('原有效区服资料在准备后变化。')
                if _read(control/'autozzz/sdk-activation.json', 256*1024) is not None:
                    raise ValueError('区服已有 SDK 切换待恢复。')
                row = dict(before=None if before is None else before.decode('utf-8'), target=None)
                row['after'] = row['before']
                if profile in targets:
                    target = targets[profile]
                    if target in (control, source):
                        raise ValueError('新快照与原资料重合。')
                    expected = sha256_token(snapshots[profile]['hash'])
                    if tree_hash(data_state(target)) != expected:
                        raise ValueError('已预检的目标快照在准备后变化。')
                    revision, protocol = binding['new']['revision'], binding['new']['protocol']
                    marker = _marker_hash(target)
                    if read_json(target/'autozzz-runtime.json', 4096, expected_digest=marker) != dict(revision=revision, protocol=protocol):
                        raise ValueError('目标快照与新发行版本不符。')
                    after = dict(format=1, kind='stable', candidate_id=None, identity_digest=None,
                                 data_id=target.name, revision=revision, protocol=protocol)
                    row.update(after=_payload(after).decode('utf-8'), target=target.name,
                               marker=marker, data=expected)
                rows[profile] = row
            plan = dict(format=1, binding=tree_hash(binding), profiles=rows)
            tx.state('prepared', sdk_commit=plan, sdk_phase='arming')
        return cls(tx)

    def __init__(self, tx):
        self.tx = tx
        self.binding = tx.data['sdk_transition']
        self.plan = tx.data['sdk_commit']
        if (self.plan['format'] != 1 or self.plan['binding'] != tree_hash(self.binding)
                or set(self.plan['profiles']) != set(PROFILES)):
            raise ValueError('两区服提交记录身份不符。')
        self.controls, self.sources = _roots(tx, self.binding)
        self.rows = self.plan['profiles']
        self.targets = {p: snapshot_path(self.controls[p], row['target'])
                        for p, row in self.rows.items() if row['target'] is not None}

    def _gate(self, profile):
        return plain(self.controls[profile]/'autozzz/sdk-activation.json')

    def _gate_bytes(self, profile):
        # Both old and current ordinary runtimes block on existence. The old
        # single-profile recovery reader rejects this distinct schema.
        return _payload(dict(format=2, kind='program-sdk-commit', job=self.tx.job.name,
                             profile=profile, plan=tree_hash(self.plan)))

    def _gates(self, *, allow_missing=False):
        present = []
        for profile in PROFILES:
            gate = _read(self._gate(profile), 256*1024)
            if gate is None and allow_missing:
                continue
            if gate != self._gate_bytes(profile):
                raise ValueError('两区服 SDK 门槛缺失或归属不符，保留现场。')
            present.append(profile)
        return present

    def _expected(self, profile, side):
        text = self.rows[profile][side]
        return None if text is None else text.encode('utf-8')

    def _read_pointer(self, profile):
        return _read(selection_path(self.controls[profile]), 8192)

    def _verify_inputs(self, *, before_only=False, restoring=False):
        for profile in PROFILES:
            before = self._expected(profile, 'before')
            allowed = (before,) if before_only else (before, self._expected(profile, 'after'))
            if self._read_pointer(profile) not in allowed:
                raise ValueError('区服 SDK 选择超出本次程序更新范围。')
            original, source = self.binding['profiles'][profile], self.sources[profile]
            if _marker_hash(source) != original['marker']:
                raise ValueError('原有效区服资料标记在联合提交期间变化。')
            if restoring:
                # Returning the selector leaves both data roots exactly as they are.
                continue
            if tree_hash(data_state(source)) != original['data']:
                raise ValueError('原有效区服资料在联合提交期间变化。')
            if profile in self.targets:
                target, row = self.targets[profile], self.rows[profile]
                if _marker_hash(target) != row['marker'] or tree_hash(data_state(target)) != row['data']:
                    raise ValueError('目标区服快照在联合提交期间变化。')

    def _directory(self, side):
        key = 'old' if side == 'before' else 'new'
        if not self.tx._marker(self.tx.target, self.binding[key]['package']):
            raise ValueError('安装目录与两区服提交方向不符。')

    def _selectors(self, side):
        for profile in PROFILES:
            if self._read_pointer(profile) != self._expected(profile, side):
                raise ValueError('区服 SDK 选择与两区服提交方向不符。')

    def _publish(self, side):
        for profile in PROFILES:
            self._gates()
            expected = self._expected(profile, side)
            if self._read_pointer(profile) == expected:
                continue
            path = selection_path(self.controls[profile])
            if expected is None:
                path.unlink(missing_ok=True)
            else:
                atomic_bytes(path, expected)
        self._selectors(side)

    def _release(self, side):
        self._directory(side)
        present = self._gates(allow_missing=True)
        for profile in present:
            # An already released profile may have started and saved new data.
            if self._read_pointer(profile) != self._expected(profile, side):
                raise ValueError('尚未释放的区服 SDK 选择已变化。')
            if side == 'after' and profile in self.targets:
                root, marker = self.targets[profile], self.rows[profile]['marker']
            else:
                root, marker = self.sources[profile], self.binding['profiles'][profile]['marker']
            if _marker_hash(root) != marker:
                raise ValueError('尚未释放的区服 SDK 资料标记已变化。')
        for profile in present:
            self._gate(profile).unlink()

    def after(self):
        with _locks([*self.controls.values(), *self.sources.values(), *self.targets.values()]):
            phase = self.tx.data['sdk_phase']
            if phase == 'committed' and self.tx.data['state'] == 'completed':
                self._release('after')
                return
            if phase == 'arming':
                if self.tx.data['state'] != 'prepared':
                    raise ValueError('目录更新状态异常，请恢复原发行。')
                verify_sdk_transition(self.tx)
                self._verify_inputs(before_only=True)
                present = self._gates(allow_missing=True)
                for profile in PROFILES:
                    if profile not in present:
                        atomic_bytes(self._gate(profile), self._gate_bytes(profile))
                self._gates()
                self.tx.state('prepared', sdk_phase='armed')
            elif phase != 'armed':
                raise ValueError('已选择恢复原发行，不能继续新版本。')
            self._gates()
            state = self.tx.data['state']
            self._verify_inputs(before_only=state == 'prepared')
            if state == 'prepared':
                verify_sdk_transition(self.tx)
                self.tx.apply(sdk_coordinated=True)
            elif state != 'installed':
                raise ValueError('安装目录交换未完成，请联合恢复原发行。')
            verify_sdk_transition(self.tx, installed=True)
            verify_package(self.tx.target)
            self._verify_inputs()
            self._publish('after')
            self._directory('after')
            self.tx.state('completed', sdk_phase='committed')
            self._release('after')

    def before(self):
        with _locks([*self.controls.values(), *self.sources.values(), *self.targets.values()]):
            phase = self.tx.data['sdk_phase']
            if phase == 'committed':
                raise ValueError('新发行已经提交，请使用反向迁移恢复旧版本。')
            if phase == 'restored' and self.tx.data['state'] == 'rolled_back':
                self._release('before')
                return
            if phase == 'arming':
                # No selector or directory was published. The second profile
                # may have saved data before its gate could be armed.
                if self.tx.data['state'] not in ('prepared', 'rolled_back'):
                    raise ValueError('原发行目录状态待核对。')
                self._directory('before')
                self._selectors('before')
                self._gates(allow_missing=True)
            elif phase in ('armed', 'restoring'):
                self._gates()
                self._verify_inputs(restoring=True)
                if phase != 'restoring':
                    self.tx.state(self.tx.data['state'], sdk_phase='restoring')
                self._publish('before')
                self.tx.recover(sdk_coordinated=True)
                self._directory('before')
                self._selectors('before')
            else:
                raise ValueError('两区服恢复状态无效。')
            self.tx.state('rolled_back', sdk_phase='restored')
            self._release('before')
