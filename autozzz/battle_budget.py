"""Durable reservation of an explicitly authorized combat energy budget."""
import json
import re
import uuid
from pathlib import Path
from .models import AutomationError

def budget_directory():
    import os
    from .config import ROOT
    return Path(os.environ["LOCALAPPDATA"])/"AutoZZZ"/"combat_budgets" if os.environ.get("LOCALAPPDATA") else ROOT/"runs"/"budgets"

class BattleBudget:
    def __init__(self, directory, identifier, server, allowance, battle_limit):
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}",identifier):
            raise ValueError("战斗预算编号仅允许字母、数字、横线和下划线。")
        self.path=Path(directory)/(identifier+".json")
        expected=dict(version=1,id=identifier,server=server,allowance=allowance,battle_limit=battle_limit)
        if self.path.exists():
            self.data=json.loads(self.path.read_text(encoding="utf-8"))
            if any(self.data.get(k)!=v for k,v in expected.items()):
                raise AutomationError("现有战斗预算与此次授权不一致；未覆盖预算或出战。")
        else:self.data=dict(**expected,entries=[]);self.save()
        for other in self.path.parent.glob("*.json"):
            if other==self.path:continue
            try:record=json.loads(other.read_text(encoding="utf-8"))
            except (ValueError,OSError):raise AutomationError("历史预算无法读取，停止出战以免重置未结算消耗。")
            if record.get("server")==server and any(e.get("status")=="pending" for e in record.get("entries",[])):
                raise AutomationError("该区服存在其他预算的未结算出战，请先续接，不得通过新预算重开。")
        entries=self.data.get("entries")
        if (not isinstance(entries,list) or any(not isinstance(e,dict) or type(e.get("cost")) is not int or e["cost"]<=0
                or e.get("status") not in ("pending","settled") or not isinstance(e.get("id"),str) for e in entries)
                or sum(e["cost"] for e in entries)>allowance or len(entries)>battle_limit
                or len({e["id"] for e in entries})!=len(entries)):
            raise AutomationError("战斗预算记录损坏，停止出战。")

    @property
    def reserved(self):return sum(e["cost"] for e in self.data["entries"])
    @property
    def remaining(self):return max(0,self.data["allowance"]-self.reserved)
    @property
    def rounds_left(self):return max(0,self.data["battle_limit"]-len(self.data["entries"]))
    @property
    def pending(self):return [e for e in self.data["entries"] if e["status"]=="pending"]

    def save(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        temp=self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(self.data,ensure_ascii=False,indent=2),encoding="utf-8")
        temp.replace(self.path)

    def reserve(self,cost,source):
        if self.pending:raise AutomationError("本预算有出战尚未结算；请续接当前战斗，不得重开。")
        if type(cost) is not int or cost<=0 or cost>self.remaining or self.rounds_left<=0:
            raise AutomationError("此次出战超出剩余电量或场次预算，已停止。")
        item=dict(id=uuid.uuid4().hex,cost=cost,source=source,status="pending")
        self.data["entries"].append(item);self.save();return item["id"]

    def settle(self,identifier,energy_after):
        entries=[e for e in self.data["entries"] if e["id"]==identifier]
        if len(entries)!=1 or entries[0]["status"]!="pending":
            raise AutomationError("战斗预算的结算记录无效。")
        entries[0].update(status="settled",energy_after=energy_after);self.save()
