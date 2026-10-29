"""独立分发运行时清单；源码版本仍由 Git 核实，包内文件逐项核实。"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat

MANIFEST_NAME = 'autozzz-bundle.json'
BUNDLE_DIRECTORY = 'onedragon'
BUNDLE_FORMAT = 1


class BundleError(RuntimeError):
    pass


def filesystem_path(path: Path) -> Path:
    """Windows 上保留完整依赖的深层路径，清单仍使用普通相对路径。"""
    path=path.absolute()
    if os.name!='nt' or str(path).startswith('\\\\?\\'):
        return path
    value=str(path)
    return Path('\\\\?\\UNC\\'+value[2:] if value.startswith('\\\\') else '\\\\?\\'+value)


def digest(path: Path) -> str:
    with filesystem_path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def relative_name(value) -> str:
    if (type(value) is not str or not value or '\\' in value or ':' in value
            or '\0' in value or any(part in ('', '.', '..') for part in value.split('/'))):
        raise BundleError('分发清单包含无效路径。')
    path = PurePosixPath(value)
    if path.is_absolute() or str(path) != value:
        raise BundleError('分发清单包含无效路径。')
    return value


def _plain_file(path: Path) -> None:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise BundleError('分发运行时包含链接或非普通文件。')


def tree_files(root: Path):
    """不跟随目录链接，不将运行时生成的 Python 缓存当作分发内容。"""
    root=filesystem_path(root)
    for folder, directories, files in os.walk(root, followlinks=False):
        for name in list(directories):
            path = Path(folder) / name
            info = path.lstat()
            if path.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise BundleError('分发运行时包含目录链接。')
            if name == '__pycache__':
                directories.remove(name)
        for name in files:
            path = Path(folder) / name
            _plain_file(path)
            if path.suffix.casefold() == '.pyc':
                continue
            relative = path.relative_to(root).as_posix()
            if relative != MANIFEST_NAME:
                yield relative_name(relative), path


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BundleError('分发清单包含重复字段。')
        result[key] = value
    return result


def read_manifest(root: Path, revision: str, protocol: int) -> dict:
    marker = root / MANIFEST_NAME
    try:
        _plain_file(marker)
        if marker.stat().st_size > 32 * 1024 * 1024:
            raise BundleError('分发清单过大。')
        metadata = json.loads(marker.read_text(encoding='utf-8'), object_pairs_hook=_object)
        if (type(metadata) is not dict or type(metadata.get('format')) is not int or metadata.get('format') != BUNDLE_FORMAT
                or metadata.get('revision') != revision or type(metadata.get('protocol')) is not int or metadata.get('protocol') != protocol
                or type(metadata.get('python_version')) is not str
                or not re.fullmatch(r'3\.11\.\d+', metadata['python_version'])):
            raise BundleError('分发运行时版本不匹配，请重新安装完整的 AutoZZZ。')
        files, resources = metadata.get('files'), metadata.get('resources')
        if type(files) is not dict or not files or type(resources) is not list:
            raise BundleError('分发运行时清单不完整。')
        seen = set()
        for name, checksum in files.items():
            relative_name(name)
            if name.casefold() in seen or type(checksum) is not str or not re.fullmatch('[0-9a-f]{64}', checksum):
                raise BundleError('分发清单路径重复或摘要无效。')
            seen.add(name.casefold())
        required = {'python/python.exe', 'python/python311.dll', 'python/LICENSE.txt',
                    'source/LICENSE', 'source/src/zzz_od/context/zzz_context.py',
                    'bridge/autozzz/onedragon_worker.py', 'bridge/autozzz/onedragon_runtime.py',
                    'bridge/autozzz/onedragon_bundle.py', 'bridge/autozzz/onedragon_diagnostics.py'}
        if not required.issubset(files):
            raise BundleError('分发运行时缺少解释器、桥接代码或上游源码。')
        seen = set()
        for name in resources:
            relative_name(name)
            if (not name.startswith(('config/', 'assets/')) or 'source/' + name not in files
                    or name.casefold() in seen):
                raise BundleError('分发资源清单无效。')
            seen.add(name.casefold())
        return metadata
    except BundleError:
        raise
    except (OSError, ValueError, TypeError) as exc:
        raise BundleError('无法读取完整的分发运行时清单。') from exc


def verify_bundle(root: Path, revision: str, protocol: int) -> dict:
    metadata = read_manifest(root, revision, protocol)
    try:
        actual = dict(tree_files(root))
        if actual.keys() != metadata['files'].keys():
            raise BundleError('分发运行时文件缺失或多出文件，请重新安装完整版本。')
        for name, checksum in metadata['files'].items():
            if digest(actual[name]) != checksum:
                raise BundleError('分发运行时文件已变更：' + name)
    except OSError as exc:
        raise BundleError('无法核实分发运行时文件。') from exc
    return metadata
