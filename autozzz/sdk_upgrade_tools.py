"""Explicitly prepared, fixed official uv release; never alters PATH or runs an installer."""
import hashlib
import io
import os
from pathlib import Path
import urllib.request
import zipfile

from .runtime_selection import plain
from .sdk_upgrade_files import atomic_json

UV_VERSION='0.9.9'
UV_URL='https://github.com/astral-sh/uv/releases/download/0.9.9/uv-x86_64-pc-windows-msvc.zip'
# Published alongside the immutable official release, 2025-11-12.
UV_CHECKSUM_URL=UV_URL+'.sha256'
UV_SHA256='b1c5d78c5cb6804e556e07eeafbcdea7378107c2bd5c342dea0cdc05a607d8e5'
UV_FILES={'uv.exe':'592c309a3d4e36fddd282bb90698889dc96259ad831eb26e41eb66b5e592b891',
          'uvw.exe':'fba250380e43ee50eedf2538ed20e4e3258c3e6b6efb83a0bebf5cab04cb5984',
          'uvx.exe':'a2d39809b68b19a62d177c99ac244ca126e509f9283aa72271fae02415683294'}


def tool_root():return plain(Path(os.environ.get('LOCALAPPDATA',Path.home()/'.local/share'))/'AutoZZZ/Tools'/('uv-'+UV_VERSION))


def verified_uv():
    root=tool_root()
    for name,checksum in UV_FILES.items():
        path=plain(root/name)
        if not path.is_file()or hashlib.sha256(path.read_bytes()).hexdigest()!=checksum:raise ValueError('固定uv工具缺失或摘要不符，请先点击“准备uv工具”。')
    return root/'uv.exe'


def prepare_uv(cancelled):
    try:return str(verified_uv())
    except ValueError:pass
    chunks=[];total=0
    with urllib.request.urlopen(UV_URL,timeout=30)as stream:
        while True:
            if cancelled.is_set():raise InterruptedError('uv下载已取消。')
            chunk=stream.read(256*1024)
            if not chunk:break
            total+=len(chunk)
            if total>40*1024**2:raise ValueError('uv压缩包超过固定大小限制。')
            chunks.append(chunk)
    raw=b''.join(chunks)
    if hashlib.sha256(raw).hexdigest()!=UV_SHA256:raise ValueError('官方uv压缩包摘要不符，未解压或执行。')
    with zipfile.ZipFile(io.BytesIO(raw))as archive:
        if set(archive.namelist())!=set(UV_FILES):raise ValueError('官方uv文件清单不符。')
        payload={name:archive.read(name)for name in UV_FILES}
    if any(hashlib.sha256(payload[name]).hexdigest()!=checksum for name,checksum in UV_FILES.items()):raise ValueError('uv可执行文件摘要不符。')
    if cancelled.is_set():raise InterruptedError('uv准备已取消。')
    root=tool_root();root.mkdir(parents=True,exist_ok=True)
    for name,content in payload.items():
        path=plain(root/name)
        # An existing unexpected executable is preserved; explicit repair must be reviewed first.
        if path.exists()and hashlib.sha256(path.read_bytes()).hexdigest()!=UV_FILES[name]:raise ValueError('现有uv工具文件已变化，保留现场，未覆盖。')
        if not path.exists():
            with path.open('xb')as stream:stream.write(content)
    atomic_json(root/'receipt.json',dict(version=UV_VERSION,url=UV_URL,checksum_url=UV_CHECKSUM_URL,archive_sha256=UV_SHA256,files=UV_FILES))
    return str(verified_uv())
