"""用已有真实截图/已安装OCR复现费用截断；不连接游戏、不下载模型。"""
import argparse
import json
from pathlib import Path
import sys
from types import SimpleNamespace as NS
from unittest.mock import patch

root=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(root),str(root/'vendor/onedragon/src')]


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--image',type=Path,required=True)
    parser.add_argument('--models',type=Path,required=True)
    parser.add_argument('--expected-available',type=int,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    import cv2,numpy as np
    from one_dragon.base.matcher.ocr import onnx_ocr_matcher as m
    from onnxocr.onnx_paddleocr import ONNXPaddleOcr
    from one_dragon.base.geometry.rectangle import Rect
    from autozzz.onedragon_charge import ChargeBudgetControl
    if args.output.exists():raise ValueError('请使用新的诊断输出目录')
    models=args.models.resolve()
    for name in ('det.onnx','rec.onnx'):
        if not (models/name).is_file():raise ValueError('缺少本地OCR模型：'+name)
    with patch.object(m,'get_ocr_model_dir',return_value=str(models)):
        parameters=m.OnnxOcrParam()
        matcher=m.OnnxOcrMatcher(parameters)
        matcher._model=ONNXPaddleOcr(**parameters.to_dict())
    frame=cv2.imdecode(np.frombuffer(args.image.read_bytes(),dtype=np.uint8),cv2.IMREAD_COLOR)
    if frame is None:raise ValueError('图像无效')
    frame=cv2.cvtColor(frame,cv2.COLOR_BGR2RGB)
    frame=cv2.resize(frame,(1920,1080))
    ctx=NS(screen_loader=NS(get_area=lambda *_:NS(rect=Rect(1590,1000,1850,1055))),ocr=matcher)
    control=ChargeBudgetControl(ctx,NS(),NS(pending=[]),100,evidence_dir=args.output/'evidence')
    operation=NS(last_screenshot=frame,plan=NS(category_name='实战模拟室',card_num='5'))
    actual=control._read_cost(operation)
    assert actual==(args.expected_available,100),actual
    old=matcher.run_ocr(frame[1000:1055,1380:1590])
    args.output.mkdir(parents=True,exist_ok=True)
    result=dict(actual=list(actual),old_ocr=list(old),source_image=str(args.image.resolve()),
        source_resolution=[1920,1080],game_input=False,budget_changed=False)
    (args.output/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    # 错配仍失败并保存来自同一标准帧的真实裁剪，不补零、不进入出战。
    operation.plan.card_num='1'
    try:control._read_cost(operation)
    except ValueError as exc:result['mismatch']=str(exc)
    else:raise AssertionError('实际100消耗不得匹配一张卡')
    assert len(control.evidence.items)==1 and not control.evidence.errors
    result['evidence']=control.evidence.status()
    (args.output/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
