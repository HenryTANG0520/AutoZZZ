"""地图、图标和路线的整组发布；异常退出后只恢复已知版本。"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import uuid


def fingerprint(raw): return "-" if raw is None else hashlib.sha256(raw).hexdigest()


class MapTransaction:
    LIMIT = 64 * 1024 * 1024

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.pending = self.inside(self.root / "config/autozzz-map-transaction.json")
        if self.pending.relative_to(self.root).as_posix().casefold()!='config/autozzz-map-transaction.json':raise ValueError('地图保存标记指向其他文件。')

    def inside(self, path):
        path = Path(path).resolve()
        if not path.is_relative_to(self.root): raise ValueError("地图资料路径超出本区服目录。")
        return path

    @staticmethod
    def _allowed(relative):
        parts=tuple(part.casefold() for part in relative.parts)
        map_asset=parts[:3]==('assets','game_data','world_patrol') and len(parts)>=6 and parts[-1] in ('road_mask.png','icon.yml')
        route=parts[:3]==('config','world_patrol_route','system') and len(parts)>=6 and relative.suffix.casefold()=='.yml'
        return map_asset or route

    def target(self, name):
        if not isinstance(name, str) or "\\" in name or ":" in name: raise ValueError("地图保存路径无效。")
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts: raise ValueError("地图保存路径无效。")
        result=self.inside(self.root/relative)
        if not self._allowed(relative) or not self._allowed(result.relative_to(self.root)):raise ValueError("只能发布地图、图标和巡逻路线。")
        if result.relative_to(self.root).as_posix().casefold()!=relative.as_posix().casefold():raise ValueError('地图保存路径被重定向。')
        return result

    def archive_dir(self,identifier):
        if not isinstance(identifier,str) or len(identifier)!=32 or any(c not in '0123456789abcdef' for c in identifier):raise ValueError('地图归档编号无效。')
        directory=self.inside(self.root/'archives/maps'/identifier)
        parts=directory.relative_to(self.root).parts
        if tuple(part.casefold() for part in parts[:2])!=('archives','maps') or len(parts)!=3 or parts[-1]!=identifier:raise ValueError('地图归档目录被重定向。')
        return directory

    def archive_read(self,directory,name):
        path=self.inside(directory/name)
        if not path.is_relative_to(directory):raise ValueError('地图归档文件指向归档目录外。')
        return self.read(path)

    def read(self, path):
        path = self.inside(path)
        if not path.exists(): return None
        if not path.is_file() or path.stat().st_size > self.LIMIT: raise ValueError("地图保存文件类型或大小无效。")
        return path.read_bytes()

    def assert_clear(self):
        if self.pending.exists(): raise ValueError("存在未完成的地图保存，请先在地图工具中恢复，再重新准备。")

    def _put(self, path, raw):
        path = self.inside(path)
        if raw is None:
            path.unlink(missing_ok=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.inside(path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp"))
        if temporary.parent!=path.parent:raise ValueError('地图临时文件指向其他目录。')
        created=False
        try:
            with temporary.open('xb') as stream:created=True;stream.write(raw)
            os.replace(temporary, path)
            created=False
        finally:
            if created:temporary.unlink(missing_ok=True)

    def load(self, identifier):
        directory=self.archive_dir(identifier)
        raw = self.archive_read(directory,'manifest.json')
        if raw is None or len(raw)>1024*1024: raise ValueError("地图归档信息缺失或过大。")
        manifest = json.loads(raw)
        if not isinstance(manifest, dict) or set(manifest)!={"version","metadata","entries"} or manifest["version"]!=1 or not isinstance(manifest["metadata"],dict):
            raise ValueError("地图归档格式无效。")
        rows = manifest["entries"]
        if not isinstance(rows,list) or not 1<=len(rows)<=1000: raise ValueError("地图归档条目无效。")
        files, seen, total = [], set(), 0
        for i,row in enumerate(rows):
            if not isinstance(row,dict) or set(row)!={"path","old_hash","new_hash"}: raise ValueError("地图归档条目无效。")
            target = self.target(row["path"])
            if target in seen: raise ValueError("地图归档包含重复文件。")
            seen.add(target)
            values=[]
            for phase in ("old","new"):
                content=self.archive_read(directory,f"{i:04d}-{phase}.bin")
                total+=len(content or b"")
                if fingerprint(content)!=row[phase+"_hash"] or total>self.LIMIT: raise ValueError("地图归档内容已变化或过大。")
                values.append(content)
            files.append((target,*values))
        return manifest, files, fingerprint(raw)

    def publish(self, writes, metadata, guard):
        self.assert_clear()
        if not writes or len(writes)>1000 or not guard(): raise ValueError("地图或路线已变化，请重新读取。")
        files=[];total=0;seen=set()
        for path, raw in writes.items():
            path=self.target(self.inside(path).relative_to(self.root).as_posix())
            if path in seen:raise ValueError("地图保存包含重复文件。")
            seen.add(path)
            if raw is not None and not isinstance(raw,bytes): raise ValueError("地图发布内容无效。")
            original=self.read(path);total+=len(original or b"")+len(raw or b"")
            if total>self.LIMIT: raise ValueError("本次地图保存内容过大。")
            files.append((path,original,raw))
        identifier=uuid.uuid4().hex
        directory=self.archive_dir(identifier);directory.mkdir(parents=True,exist_ok=False)
        entries=[]
        for i,(path,old,new) in enumerate(files):
            for phase,content in (("old",old),("new",new)):
                if content is not None:
                    with (directory/f"{i:04d}-{phase}.bin").open('xb') as stream:stream.write(content)
            entries.append(dict(path=path.relative_to(self.root).as_posix(),old_hash=fingerprint(old),new_hash=fingerprint(new)))
        raw=json.dumps(dict(version=1,metadata=metadata,entries=entries),ensure_ascii=False,sort_keys=True).encode('utf-8')
        with (directory/'manifest.json').open('xb') as stream:stream.write(raw)
        if not guard() or any(fingerprint(self.read(path))!=fingerprint(old) for path,old,_ in files): raise ValueError("准备发布时地图或路线已变化，原件保持不变。")
        self._put(self.pending,json.dumps(dict(id=identifier,manifest_hash=fingerprint(raw))).encode())
        try:
            for index,(path,old,new) in enumerate(files):
                for j,(check,previous,next_value) in enumerate(files):
                    expected=next_value if j<index else previous
                    if fingerprint(self.read(check))!=fingerprint(expected): raise ValueError("发布期间文件被外部修改。")
                self._put(path,new)
            if any(fingerprint(self.read(path))!=fingerprint(new) for path,_,new in files): raise ValueError("发布结果与保存内容不一致。")
            self.pending.unlink()
        except Exception as exc:
            try:self.recover()
            except Exception as recovery:raise RuntimeError(f"地图保存中断，恢复未完成：{recovery}。请保留现场并恢复保存。") from exc
            raise
        return identifier

    def recover(self):
        raw=self.read(self.pending)
        if raw is None:return None
        if len(raw)>4096:raise ValueError("地图保存标记无效。")
        marker=json.loads(raw)
        if not isinstance(marker,dict) or set(marker)!={"id","manifest_hash"}:raise ValueError("地图保存标记无效。")
        manifest,files,digest=self.load(marker['id'])
        if marker['manifest_hash']!=digest:raise ValueError("地图归档信息已变化，暂时不能恢复。")
        for path,old,new in files:
            if fingerprint(self.read(path)) not in (fingerprint(old),fingerprint(new)):
                raise ValueError("地图或路线已有外部修改，不能覆盖："+str(path.relative_to(self.root)))
        for path,old,new in reversed(files):
            current=fingerprint(self.read(path))
            if current==fingerprint(old):continue
            if current!=fingerprint(new):raise ValueError("恢复期间文件被外部修改。")
            self._put(path,old)
        if any(fingerprint(self.read(path))!=fingerprint(old) for path,old,_ in files):raise ValueError("地图恢复结果不一致。")
        self.pending.unlink()
        return manifest['metadata']
