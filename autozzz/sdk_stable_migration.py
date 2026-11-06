"""Verify the old-default receipt supplied by the reviewed release transaction."""
import re
from pathlib import Path

from .onedragon_bundle import relative_name
from .onedragon_runtime import PROTOCOL_VERSION
from .runtime_selection import FORMAT, commit, read_json, tree_hash


def receipt_path(stable_data, revision):
    return Path(stable_data)/'autozzz/sdk-defaults'/(commit(revision)+'.json')


def sha256_token(value):
    if type(value) is not str or re.fullmatch('[0-9a-f]{64}', value) is None:
        raise ValueError('稳定 SDK 迁移摘要无效。')
    return value


def frozen_defaults(stable_data, revision, expected_digest):
    # Parse the same bounded bytes that were checked against the frozen digest.
    saved = read_json(receipt_path(stable_data, revision), 8*1024**2,
                      expected_digest=sha256_token(expected_digest))
    if (type(saved) is not dict or set(saved) != {'format','revision','protocol','origin','files','resource_identity'}
            or saved['format'] != FORMAT or saved['revision'] != revision or saved['protocol'] != PROTOCOL_VERSION):
        raise ValueError('旧稳定 SDK 资源收据身份无效。')
    origin, files = saved['origin'], saved['files']
    if type(origin) is not dict or set(origin) != {'kind','identity'}:
        raise ValueError('旧稳定 SDK 资源来源无效。')
    if origin['kind'] == 'verified_bundle':
        sha256_token(origin['identity'])
    elif origin['kind'] == 'verified_git_checkout_bytes':
        commit(origin['identity'])
    else:
        raise ValueError('旧稳定 SDK 资源来源无效。')
    if type(files) is not dict or not 0 < len(files) <= 50000 or tree_hash(files) != saved['resource_identity']:
        raise ValueError('旧稳定 SDK 资源摘要表无效。')
    seen = set()
    for name, checksum in files.items():
        relative_name(name)
        sha256_token(checksum)
        if not name.startswith(('assets/','config/')) or name.casefold() in seen:
            raise ValueError('旧稳定 SDK 资源路径无效。')
        seen.add(name.casefold())
    return files
