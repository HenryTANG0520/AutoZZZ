"""Bind verified release manifests and both old SDK profiles to an update job."""
from contextlib import nullcontext
from hashlib import sha256

from .onedragon_bundle import MANIFEST_NAME, digest, filesystem_path
from .onedragon_runtime import PROTOCOL_VERSION, RuntimePaths
from .program_package import MANIFEST, verify_package
from .run_lock import profile_lock
from .runtime_selection import FORMAT, plain, read_json, resolve_selection, selection_path, tree_hash
from .sdk_upgrade_files import atomic_bytes, atomic_json, data_state
from .sdk_stable_migration import receipt_path


PROFILES = ('cn', 'global')
RECEIPT_NAME = 'sdk-old-defaults.json'
_BUNDLE_KEY = 'onedragon/' + MANIFEST_NAME


def _release(root, expected, *, verify=False, strict=False):
    package = read_json(root/MANIFEST, 64*1024**2, expected_digest=expected)
    if verify and verify_package(root, strict=strict) != package:
        raise ValueError('程序包与更新记录不符。')
    bundle_hash = package['files'][_BUNDLE_KEY]
    bundle = read_json(root/_BUNDLE_KEY, expected_digest=bundle_hash)
    if (package['runtime_revision'] != bundle['revision'] or package['protocol'] != bundle['protocol']
            or bundle['protocol'] != PROTOCOL_VERSION):
        raise ValueError('程序包与 SDK 协议不符。')
    return dict(package=expected, bundle=bundle_hash, revision=bundle['revision'],
                protocol=bundle['protocol']), bundle


def _receipt(old, bundle):
    files = {name: bundle['files']['source/' + name] for name in bundle['resources']}
    return dict(format=FORMAT, revision=old['revision'], protocol=old['protocol'],
                origin=dict(kind='verified_bundle', identity=old['bundle']),
                files=files, resource_identity=tree_hash(files))


def _profile(old_root, old, profile):
    paths = RuntimePaths.discover(old_root, profile, use_selection=False)
    control = plain(paths.data.resolve())
    with profile_lock(control):
        pointer = selection_path(control)
        before = digest(pointer) if pointer.exists() else '-'
        selected = resolve_selection(control, stable_source=paths.source, stable_python=paths.python,
            stable_bridge=paths.bridge_root, stable_bundle=paths.bundle, stable_revision=old['revision'],
            protocol=old['protocol'], verify_files=False)
        source = selected.data if selected is not None else control
        with (nullcontext() if source == control else profile_lock(source)):
            marker = plain(source/'autozzz-runtime.json')
            state = data_state(source)
            if selected is None and not marker.exists() and not state:
                return dict(kind='empty', control=str(control), source=str(source), selector=before,
                            marker=None, data=tree_hash(state))
            revision = selected.revision if selected is not None else old['revision']
            marker_hash = digest(marker)
            if read_json(marker, 4096, expected_digest=marker_hash) != dict(revision=revision, protocol=old['protocol']):
                raise ValueError('原资料标记与已选 SDK 不符。')
            if (digest(pointer) if pointer.exists() else '-') != before:
                raise ValueError('冻结期间 SDK 选择发生变化。')
            kind = 'candidate' if selected is not None and selected.kind == 'candidate' else 'stable'
            return dict(kind=kind, control=str(control), source=str(source), selector=before,
                        marker=marker_hash, data=tree_hash(state))


def bind_sdk_transition(tx):
    """Call during verified update handoff while the old total task lock is held."""
    if tx.data['state'] != 'prepared' or 'sdk_transition' in tx.data:
        raise ValueError('更新事务已经过准备，不能重绑 SDK 迁移。')
    old, _ = _release(tx.target, tx.data['old_manifest'])
    new, _ = _release(tx.candidate, tx.data['new_manifest'])
    if old['revision'] == new['revision']:
        return None
    old, old_bundle = _release(tx.target, tx.data['old_manifest'], verify=True)
    new, _ = _release(tx.candidate, tx.data['new_manifest'], verify=True, strict=True)
    profiles = {profile: _profile(tx.target, old, profile) for profile in PROFILES}
    if len({row['control'] for row in profiles.values()}) != len(PROFILES):
        raise ValueError('两个区服控制目录重合，未准备迁移。')
    path = plain(tx.job/RECEIPT_NAME)
    if path.exists():
        raise ValueError('旧默认资源收据已存在，未覆盖。')
    proof = _receipt(old, old_bundle)
    if not proof['files']:
        raise ValueError('旧发行缺少默认资源清单。')
    atomic_json(path, proof)
    binding = dict(format=FORMAT, old=old, new=new, receipt=digest(path), profiles=profiles)
    tx.state('prepared', sdk_transition=binding)
    return binding


def verify_sdk_transition(tx, *, installed=False):
    binding = tx.data.get('sdk_transition')
    if (type(binding) is not dict or set(binding) != {'format','old','new','receipt','profiles'}
            or binding['format'] != FORMAT or set(binding['profiles']) != set(PROFILES)):
        raise ValueError('程序更新未绑定 SDK 迁移。')
    old_root = tx.backup if installed else tx.target
    new_root = tx.target if installed else tx.candidate
    old, bundle = _release(old_root, tx.data['old_manifest'])
    new, _ = _release(new_root, tx.data['new_manifest'])
    path = tx.job/RECEIPT_NAME
    proof = read_json(path, 8*1024**2, expected_digest=binding['receipt'])
    if old != binding['old'] or new != binding['new'] or proof != _receipt(old, bundle):
        raise ValueError('程序更新绑定的发行或旧默认资源已变化。')
    return binding


def verify_sdk_profile(tx, profile, *, installed=False):
    if profile not in PROFILES:
        raise ValueError('不支持此 SDK 区服。')
    binding = verify_sdk_transition(tx, installed=installed)
    old_root = tx.backup if installed else tx.target
    if _profile(old_root, binding['old'], profile) != binding['profiles'][profile]:
        raise ValueError('旧区服选择或资料在程序更新准备后发生变化。')
    return binding['profiles'][profile]


def stage_sdk_receipt(tx, profile, *, installed=True):
    """Publish the already-bound receipt for the SDK control worker to verify again."""
    binding = verify_sdk_transition(tx, installed=installed)
    profile_state = verify_sdk_profile(tx, profile, installed=installed)
    if profile_state['kind'] != 'stable':
        return profile_state
    release = tx.target if installed else tx.candidate
    control = plain(RuntimePaths.discover_sdk_control(release, profile).data.resolve())
    if str(control)!=profile_state['control']:
        raise ValueError('区服控制目录在收据交接期间变化。')
    path = plain(receipt_path(control, binding['old']['revision']))
    with profile_lock(control):
        if path.exists():
            if digest(path) != binding['receipt']:
                raise ValueError('区服旧默认资源收据与程序更新记录不符，未覆盖。')
        else:
            with filesystem_path(plain(tx.job/RECEIPT_NAME)).open('rb') as stream:
                data = stream.read(8*1024**2+1)
            if len(data) > 8*1024**2:
                raise ValueError('旧默认资源收据过大。')
            if sha256(data).hexdigest() != binding['receipt']:
                raise ValueError('旧默认资源收据在交接期间变化。')
            atomic_bytes(path, data)
    return profile_state
