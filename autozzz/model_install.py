"""模型压缩包校验和整目录安装；中断后恢复已知原版本。"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import uuid
import zipfile


MAX_MODEL_BYTES = 1024 * 1024 * 1024
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
FAMILIES = {'flash_classifier', 'hollow_zero_event', 'lost_void_det', 'onnx_ocr'}


def name_valid(name: str) -> bool:
    reserved = {'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))}
    return (isinstance(name,str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}',name) is not None
            and not name.endswith('.') and name.split('.')[0].upper() not in reserved)


def file_hash(raw: bytes | None) -> str:
    return '-' if raw is None else hashlib.sha256(raw).hexdigest()


def extract_model(archive: Path, directory: Path, required: list[str], ocr: bool, cancelled) -> None:
    """压缩包只贡献必要模型文件；任何危险条目都会拒绝整包。"""
    if archive.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError('模型压缩包超过 512 MB。')
    directory = directory.absolute()
    if directory != directory.resolve():raise ValueError('模型临时目录被重定向。')
    expected = directory.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    total, seen, wanted = 0, set(), []
    with zipfile.ZipFile(archive) as package:
        if len(package.infolist()) > 4096:
            raise ValueError('模型压缩包条目过多。')
        for item in package.infolist():
            text = item.orig_filename
            path = PurePosixPath(text)
            mode = item.external_attr >> 16
            if (not text or '\\' in text or ':' in text or path.is_absolute() or '..' in path.parts
                or any(part in ('', '.') or part.endswith(('.', ' ')) or any(ord(c) < 32 for c in part)
                       or part.split('.')[0].upper() in {'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))} for part in path.parts)
                or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)) or item.flag_bits & 1):
                raise ValueError('模型压缩包包含无效路径或文件类型。')
            key = str(path).casefold()
            if key in seen:
                raise ValueError('模型压缩包包含重复路径。')
            seen.add(key)
            total += item.file_size
            if total > MAX_MODEL_BYTES or item.file_size < 0:
                raise ValueError('模型解压后超过 1 GB。')
            if not item.is_dir() and (text in required or ocr and len(path.parts) == 1 and text.endswith('_dict.txt')):
                if item.file_size == 0:
                    raise ValueError('模型必要文件为空。')
                wanted.append(item)
        names = {item.filename for item in wanted}
        if not set(required) <= names or ocr and sum(name.endswith('_dict.txt') for name in names) != 1:
            raise ValueError('压缩包缺少必要模型文件，或 OCR 字典数量不正确。')
        for item in wanted:
            if cancelled():
                raise InterruptedError('已取消模型解压。')
            target = directory / item.filename
            if directory.resolve() != expected or target.resolve().parent != expected:
                raise ValueError('模型解压路径被重定向。')
            with package.open(item) as source, target.open('xb') as output:
                written = 0
                while chunk := source.read(1024 * 1024):
                    if cancelled():
                        raise InterruptedError('已取消模型解压。')
                    written += len(chunk)
                    if written > item.file_size:
                        raise ValueError('模型条目大小不符。')
                    output.write(chunk)
                if written != item.file_size:
                    raise ValueError('模型条目不完整。')


class ModelInstall:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.pending = self.safe(self.root/'config/autozzz-model-transaction.json')
        self.version = self.safe(self.root/'config/model_files.yml')

    def safe(self, path: Path) -> Path:
        absolute = Path(path).absolute()
        result = absolute.resolve()
        if not result.is_relative_to(self.root) or str(absolute).casefold() != str(result).casefold():
            raise ValueError('模型资料路径超出本区服目录或被重定向。')
        return result

    def target(self, family: str, model: str) -> Path:
        if family not in FAMILIES or not name_valid(model):
            raise ValueError('模型名称或类别无效。')
        return self.safe(self.root/'assets/models'/family/model)

    def job_dir(self, identifier: str) -> Path:
        if not isinstance(identifier, str) or not re.fullmatch('[0-9a-f]{32}', identifier):
            raise ValueError('模型安装编号无效。')
        return self.safe(self.root/'.autozzz-downloads'/identifier)

    def archive_dir(self, identifier: str) -> Path:
        self.job_dir(identifier)
        return self.safe(self.root/'archives/models'/identifier)

    def digest(self, directory: Path) -> str:
        directory = self.safe(directory)
        if not directory.exists():
            return '-'
        if not directory.is_dir():
            raise ValueError('模型目录不是文件夹。')
        values, size = [], 0
        for path in sorted(directory.rglob('*')):
            checked = self.safe(path)
            if checked.is_dir():
                continue
            if not checked.is_file():
                raise ValueError('模型目录包含非普通文件。')
            size += checked.stat().st_size
            if size > 2*MAX_MODEL_BYTES or len(values) >= 4096:
                raise ValueError('现有模型目录过大。')
            digest = hashlib.sha256()
            with checked.open('rb') as stream:
                while block := stream.read(1024*1024): digest.update(block)
            values.append((str(path.relative_to(directory)), digest.hexdigest()))
        return file_hash(json.dumps(values, sort_keys=True).encode())

    def read(self, path: Path) -> bytes | None:
        path = self.safe(path)
        if not path.exists(): return None
        if not path.is_file() or path.stat().st_size > 1024*1024:
            raise ValueError('模型安装记录无效或过大。')
        return path.read_bytes()

    def put(self, path: Path, raw: bytes | None) -> None:
        path = self.safe(path)
        if raw is None:
            path.unlink(missing_ok=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.safe(path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp'))
        created = False
        try:
            with temporary.open('xb') as stream:
                created = True
                stream.write(raw)
            os.replace(temporary, self.safe(path))
            created = False
        finally:
            if created: temporary.unlink(missing_ok=True)

    def move(self, origin: Path, target: Path) -> None:
        origin, target = self.safe(origin), self.safe(target)
        if not origin.is_dir() or target.exists():
            raise ValueError('模型目录替换条件已变化。')
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(origin, target)

    def assert_clear(self) -> None:
        if self.safe(self.pending).exists():
            raise ValueError('存在未完成的模型安装，请先在资源下载窗口恢复。')

    def publish(self, family: str, model: str, identifier: str) -> str:
        self.assert_clear()
        target = self.target(family, model)
        stage = self.safe(self.job_dir(identifier)/'content')
        if not stage.is_dir(): raise ValueError('模型临时目录缺失。')
        old, new = self.digest(target), self.digest(stage)
        before = self.read(self.version)
        if before is not None and len(before)>64*1024:
            raise ValueError('模型版本记录过大。')
        after = ('version: '+identifier+'\n').encode()
        record = dict(version=1, id=identifier, family=family, model=model, old=old, new=new,
                      before=None if before is None else base64.b64encode(before).decode(), after=base64.b64encode(after).decode())
        archive = self.archive_dir(identifier)
        archive.mkdir(parents=True, exist_ok=False)
        raw = json.dumps(record, sort_keys=True).encode()
        with (archive/'manifest.json').open('xb') as stream: stream.write(raw)
        if self.digest(target) != old or self.read(self.version) != before:
            raise ValueError('安装前模型文件已被修改。')
        self.put(self.pending, json.dumps(dict(id=identifier, hash=file_hash(raw))).encode())
        try:
            if old != '-': self.move(target, archive/'previous')
            self.move(stage, target)
            if self.digest(target) != new: raise ValueError('模型安装结果不一致。')
            if self.read(self.version) != before: raise ValueError('安装期间版本记录已变化。')
            self.put(self.version, after)
            self.pending.unlink()
        except Exception as exc:
            try: self.recover()
            except Exception as failure:
                raise RuntimeError('模型安装中断，恢复未完成：'+str(failure)) from exc
            raise
        return identifier

    def recover(self) -> bool:
        raw = self.read(self.pending)
        if raw is None: return False
        marker = json.loads(raw)
        if not isinstance(marker,dict) or set(marker) != {'id','hash'}:
            raise ValueError('模型安装标记无效。')
        archive = self.archive_dir(marker['id'])
        source = self.read(archive/'manifest.json')
        if source is None or file_hash(source) != marker['hash']:
            raise ValueError('模型安装归档已变化。')
        record = json.loads(source)
        if (not isinstance(record,dict) or set(record) != {'version','id','family','model','old','new','before','after'}
            or record['version'] != 1 or record['id'] != marker['id']):
            raise ValueError('模型安装归档格式无效。')
        target = self.target(record['family'],record['model'])
        previous, rejected = self.safe(archive/'previous'), self.safe(archive/'unpublished')
        before = None if record['before'] is None else base64.b64decode(record['before'],validate=True)
        after = base64.b64decode(record['after'],validate=True)
        current, saved = self.digest(target), self.digest(previous)
        if self.read(self.version) not in (before,after):
            raise ValueError('模型版本记录已有外部修改，不能恢复。')
        old, new = record['old'],record['new']
        untouched = old != '-' and current == old and saved == '-'
        if not untouched and (saved != old or current not in ('-',new)):
            raise ValueError('模型目录已有外部修改，不能覆盖。')
        if current == new and not untouched:
            self.move(target,rejected)
        if not untouched and old != '-': self.move(previous,target)
        if self.digest(target) != old: raise ValueError('模型原目录恢复不一致。')
        self.put(self.version,before)
        self.pending.unlink()
        return True
