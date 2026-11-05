"""保存费用校验所用帧与 OCR；不截图、不输入、不修改战斗预算。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import uuid
import math


FILES=('frame.png','crop.png','ocr.json')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def ocr_records(raw):
    records=[]
    for text,matches in raw.items():
        boxes=[]
        for match in list(getattr(matches,'arr',[]) or [matches.max]):
            if match is None:continue
            confidence=float(match.confidence)
            boxes.append(dict(confidence=confidence if math.isfinite(confidence) else None,
                box=[int(getattr(match,key,0)) for key in ('x','y','w','h')]))
        records.append(dict(text=str(text),matches=boxes))
    return records


class ChargeEvidence:
    def __init__(self,root):
        self.root=Path(root)
        self.items=[]
        self.errors=[]

    def record(self,frame,crop,rect,raw,reason,plan):
        # 一次运行最多保存四组；覆盖重配前后，不按每一重试无限写图。
        if len(self.items)+len(self.errors)>=4:return
        try:
            import cv2
            key=uuid.uuid4().hex
            directory=self.root/key
            directory.mkdir(parents=True,exist_ok=False)
            metadata=dict(format=1,kind='charge_cost_validation',reason=str(reason),
                frame_shape=list(frame.shape),crop_shape=list(crop.shape),
                crop_rect=[rect.x1,rect.y1,rect.x2,rect.y2],
                color_space='RGB (SDK screenshot convention)',
                frame_source='operation.last_screenshot (SDK recognition frame; no new capture)',
                plan=dict(category=plan.category_name,card_num=getattr(plan,'card_num',None)),
                ocr=ocr_records(raw))
            payload={}
            for name,pixels in (('frame.png',frame),('crop.png',crop)):
                ok,encoded=cv2.imencode('.png',cv2.cvtColor(pixels,cv2.COLOR_RGB2BGR))
                if not ok:raise ValueError('无法编码费用诊断图')
                payload[name]=encoded.tobytes()
            payload['ocr.json']=json.dumps(metadata,ensure_ascii=False,indent=2,allow_nan=False).encode('utf-8')
            for name,data in payload.items():(directory/name).write_bytes(data)
            self.items.append(dict(id=key,reason=str(reason),files={name:digest(data) for name,data in payload.items()}))
        except Exception as exc:
            # 保存诊断失败也不能把费用失败变成可出战。
            self.errors.append(str(exc))

    def status(self):
        return [dict(item,files=dict(item['files'])) for item in self.items]


def collect_charge_evidence(state,data_root,report_dir):
    """仅收集 SDK 自己生成且哈希匹配的固定文件，放进同一轮报告。"""
    budget=state.get('budget',{})
    records=budget.get('evidence',[])
    if not records:return []
    job_id=state.get('job_id','')
    if not isinstance(job_id,str) or re.fullmatch('[a-f0-9]{32}',job_id) is None:
        raise ValueError('费用证据任务编号无效')
    if not isinstance(records,list) or len(records)>4:raise ValueError('费用证据数量无效')
    if data_root is None:raise ValueError('费用证据缺少SDK资料目录')
    source=Path(data_root)/'autozzz/charge-evidence'/job_id
    target=Path(report_dir)/'evidence/charge'
    collected=[]
    for record in records:
        if not isinstance(record,dict):raise ValueError('费用证据记录无效')
        key=record.get('id','')
        if not isinstance(key,str) or re.fullmatch('[a-f0-9]{32}',key) is None:
            raise ValueError('费用证据编号无效')
        checksums=record.get('files',{})
        if not isinstance(checksums,dict) or set(checksums)!=set(FILES):raise ValueError('费用证据文件清单无效')
        contents={}
        for name in FILES:
            path=source/key/name
            if any(p.is_symlink() or getattr(p.lstat(),'st_file_attributes',0)&0x400
                   for p in (source,source/key,path)):
                raise ValueError('费用证据不能是链接')
            if not path.is_file() or path.stat().st_size>24*1024**2:
                raise ValueError('费用证据文件缺失或超限')
            content=path.read_bytes()
            if digest(content)!=checksums[name]:raise ValueError('费用证据摘要不符')
            contents[name]=content
        destination=target/key
        destination.mkdir(parents=True,exist_ok=True)
        if destination.is_symlink() or getattr(destination.lstat(),'st_file_attributes',0)&0x400:
            raise ValueError('报告证据目录不能是链接')
        for name,content in contents.items():
            saved=destination/name
            if saved.exists():
                if saved.is_symlink() or digest(saved.read_bytes())!=checksums[name]:
                    raise ValueError('报告证据已存在且摘要不符')
            else:saved.write_bytes(content)
        collected.append(dict(record,paths={name:str((destination/name).resolve()) for name in FILES}))
    return collected
