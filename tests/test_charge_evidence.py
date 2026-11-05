import hashlib
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from autozzz.charge_evidence import ChargeEvidence,collect_charge_evidence
from autozzz.onedragon_charge import ChargeBudgetControl


class ChargeEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.job='a'*32
        self.store=ChargeEvidence(self.root/'autozzz/charge-evidence'/self.job)
        self.frame=np.arange(80*120*3,dtype=np.uint8).reshape(80,120,3)
        self.crop=self.frame[20:60,50:100]
        self.rect=NS(x1=50,y1=20,x2=100,y2=60)
        self.raw={'296/10':NS(max=NS(confidence=.9999,x=5,y=6,w=30,h=15))}
        self.plan=NS(category_name='实战模拟室',card_num='5')

    def record(self):
        self.store.record(self.frame,self.crop,self.rect,self.raw,'画面296/10，计划100',self.plan)
        return dict(job_id=self.job,budget=dict(evidence=self.store.status()))

    def test_exact_frame_crop_and_unmodified_ocr_are_collected_into_report(self):
        state=self.record();rows=collect_charge_evidence(state,self.root,self.root/'report')
        row,=rows
        for name,expected in [('frame.png',self.frame),('crop.png',self.crop)]:
            pixels=cv2.imdecode(np.frombuffer(Path(row['paths'][name]).read_bytes(),dtype=np.uint8),cv2.IMREAD_COLOR)
            np.testing.assert_array_equal(cv2.cvtColor(pixels,cv2.COLOR_BGR2RGB),expected)
        metadata=json.loads(Path(row['paths']['ocr.json']).read_text(encoding='utf-8'))
        self.assertEqual(metadata['crop_rect'],[50,20,100,60])
        self.assertEqual(metadata['ocr'][0]['text'],'296/10')
        self.assertEqual(metadata['ocr'][0]['matches'][0]['box'],[5,6,30,15])
        self.assertEqual(collect_charge_evidence(state,self.root,self.root/'report'),rows)

    def test_original_pixels_are_not_mutated_and_evidence_is_bounded(self):
        original=self.frame.copy()
        for _ in range(10):self.record()
        self.assertEqual(len(self.store.items),4)
        np.testing.assert_array_equal(self.frame,original)

    def test_tampered_evidence_is_not_attached(self):
        state=self.record();key=state['budget']['evidence'][0]['id']
        (self.store.root/key/'crop.png').write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError,'摘要不符'):
            collect_charge_evidence(state,self.root,self.root/'report')
        self.assertFalse((self.root/'report').exists())

    def test_invalid_identifiers_cannot_escape_evidence_root(self):
        state=self.record()
        for key,value in [('job_id','../escape'),('job_id',None)]:
            invalid=dict(state,**{key:value})
            with self.assertRaises(ValueError):collect_charge_evidence(invalid,self.root,self.root/'report')
        state['budget']['evidence'][0]['id']='../escape'
        with self.assertRaises(ValueError):collect_charge_evidence(state,self.root,self.root/'report')

    def test_empty_diagnostics_need_no_profile_or_report_directory(self):
        self.assertEqual(collect_charge_evidence({},None,None),[])

    def test_write_failure_preserves_cost_failure(self):
        ctx=NS(screen_loader=NS(get_area=lambda *_:NS(rect=NS(x1=90,y1=20,y2=60))),
               ocr=NS(run_ocr=lambda _:self.raw))
        adapter=ChargeBudgetControl(ctx,NS(),NS(pending=[]),100,evidence_dir=self.store.root)
        operation=NS(last_screenshot=self.frame,plan=self.plan)
        modules={'one_dragon.utils':NS(cv2_utils=NS(crop_image_only=lambda *_:self.crop)),
            'one_dragon.base.geometry.rectangle':NS(Rect=lambda *a:NS(x1=a[0],y1=a[1],x2=a[2],y2=a[3]))}
        with patch.dict('sys.modules',modules),patch('autozzz.charge_evidence.Path.mkdir',side_effect=OSError('disk full')):
            with self.assertRaisesRegex(ValueError,'296/10'):adapter._read_cost(operation)
        self.assertIn('disk full',adapter.evidence.errors[0]);self.assertFalse(adapter.evidence.items)

    def test_correct_cost_never_uses_planned_value_to_repair_bad_ocr(self):
        adapter=ChargeBudgetControl(NS(),NS(),NS(pending=[]),100)
        operation=NS(plan=self.plan)
        for text in ['296/10','296/1','296/80']:
            with self.assertRaises(ValueError):adapter._validate_cost(operation,{text:NS(max=NS(confidence=.99))})
        self.assertEqual(adapter._validate_cost(operation,{'296/100':NS(max=NS(confidence=.99))}),(296,100))

    def test_ambiguous_and_low_confidence_text_never_allows_deployment(self):
        adapter=ChargeBudgetControl(NS(),NS(),NS(pending=[]),100)
        for raw in [{},{'296/100':NS(max=NS(confidence=.6))},
                    {'296/100':NS(max=NS(confidence=.99)),'297/100':NS(max=NS(confidence=.99))}]:
            with self.assertRaises(ValueError):adapter._validate_cost(NS(plan=self.plan),raw)

    def test_no_clobber_of_existing_report_evidence(self):
        state=self.record();rows=collect_charge_evidence(state,self.root,self.root/'report')
        Path(rows[0]['paths']['ocr.json']).write_text('user file',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'已存在'):
            collect_charge_evidence(state,self.root,self.root/'report')
        self.assertEqual(Path(rows[0]['paths']['ocr.json']).read_text(),'user file')

    def test_normal_session_terminal_report_attaches_files_without_new_capture(self):
        from autozzz.onedragon_session import RuntimeSession
        state=dict(self.record(),state='failed',busy=False)
        session=object.__new__(RuntimeSession)
        session.paths=NS(data=self.root);session.emit=lambda _:None
        session.report_lock=threading.RLock();session.report_root=self.root/'reports'
        session.parameters={};session.report=None
        session._begin_report(dict(app_id='charge_plan'))
        session._finish_report(state)
        report=json.loads((session.report_dir/'report.json').read_text(encoding='utf-8'))
        self.assertEqual(report['status'],'failed')
        self.assertFalse(report['game_input_verified'])
        self.assertTrue(Path(report['evidence'][0]['paths']['crop.png']).is_file())
        self.assertEqual(report['execution']['budget']['evidence'],self.store.status())
