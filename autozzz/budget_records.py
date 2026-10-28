"""读取和核对持久预算；未知消耗按已预留上限记账，不退款或标记获胜。"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import uuid

from .run_lock import RunLock


class BudgetRecords:
    def __init__(self,directory:Path):self.directory=Path(directory)

    def _read(self,path:Path):
        if path.resolve().parent!=self.directory.resolve() or path.stat().st_size>2*1024*1024:
            raise ValueError("预算文件路径或大小无效。")
        content=path.read_bytes()
        data=json.loads(content)
        if (not isinstance(data,dict) or data.get("version")!=1 or data.get("id")!=path.stem
                or data.get("server") not in ("cn","global")
                or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}",path.stem)
                or any(type(data.get(key)) is not int or data[key]<=0 for key in ("allowance","battle_limit"))):
            raise ValueError("预算记录无效："+path.name)
        entries=data.get("entries")
        if (not isinstance(entries,list) or any(not isinstance(e,dict) or type(e.get("cost")) is not int or e["cost"]<=0
                or e.get("status") not in ("pending","settled") or not isinstance(e.get("id"),str) for e in entries)
                or sum(e["cost"] for e in entries)>data["allowance"] or len(entries)>data["battle_limit"]
                or len({e["id"] for e in entries})!=len(entries)):
            raise ValueError("预算条目无效："+path.name)
        return data,hashlib.sha256(content).hexdigest()

    def pending(self,server:str):
        if server not in ("cn","global"):raise ValueError("区服无效。")
        with RunLock():
            result=[]
            for path in sorted(self.directory.glob("*.json")):
                data,revision=self._read(path)
                entries=[entry for entry in data["entries"] if entry["status"]=="pending"]
                if data["server"]==server and entries:
                    result.append(dict(id=data["id"],revision=revision,pending=len(entries),cost=sum(e["cost"] for e in entries)))
            return result

    def account_reserved_upper_bound(self,identifier:str,revision:str):
        if not isinstance(identifier,str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}",identifier):raise ValueError("预算编号无效。")
        with RunLock():
            target=self.directory/(identifier+".json")
            data,current=self._read(target)
            if current!=revision:raise ValueError("预算记录已变化，请重新读取后核对。")
            entries=[entry for entry in data["entries"] if entry["status"]=="pending"]
            if not entries:raise ValueError("此预算没有待核对出战。")
            for entry in entries:
                entry.update(status="settled",energy_after=None,resolution="reserved_upper_bound",battle_result="unknown",
                             reconciled_at=datetime.now().astimezone().isoformat())
            temporary=target.with_name(target.name+"."+uuid.uuid4().hex+".tmp")
            try:
                temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
                temporary.replace(target)
            finally:temporary.unlink(missing_ok=True)
            return dict(id=identifier,cost=sum(e["cost"] for e in entries),pending=0)
