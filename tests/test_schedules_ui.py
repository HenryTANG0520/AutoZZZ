from dataclasses import replace
import unittest
from autozzz.schedules import Schedule
from autozzz.schedules_ui import editor_plan


class ScheduleEditorTests(unittest.TestCase):
    def values(self):
        return dict(name='每日',mode='原每日流程',server='国服',trigger='每天',time='09:30',date='',
            instance='1',app_id='charge_plan',timeout='600',allowance='',battles='',maximum='')

    def test_edit_revokes_old_authorization_before_review(self):
        original=Schedule(authorization={'old':'review'})
        plan=editor_plan(original,self.values(),dict(highest=True,wake=False,missed=False),[0,1])
        self.assertEqual(plan.id,original.id);self.assertEqual(plan.authorization,{})
        self.assertFalse(plan.enabled);self.assertIsNone(plan.instance);self.assertEqual(plan.app_id,'one_dragon')

    def test_native_budget_parsing_and_incomplete_refusal(self):
        values=self.values();values.update(mode='指定账号应用',allowance='40',battles='2',maximum='20')
        plan=editor_plan(Schedule(),values,dict(highest=False,wake=False,missed=False),[0])
        self.assertEqual((plan.instance,plan.allowance,plan.battles,plan.maximum),(1,40,2,20))
        values['maximum']=''
        with self.assertRaises(ValueError):editor_plan(Schedule(),values,dict(highest=False,wake=False,missed=False),[0])

    def test_invalid_time_does_not_register(self):
        values=self.values();values['time']='25:00'
        with self.assertRaises(ValueError):editor_plan(Schedule(),values,dict(highest=False,wake=False,missed=False),[0])

    def test_disabled_fields_do_not_block_daily_or_logon(self):
        values=self.values();values.update(timeout='unused',date='unused',trigger='登录时',time='unused')
        plan=editor_plan(Schedule(),values,dict(highest=False,wake=False,missed=False),[])
        self.assertEqual(plan.timeout,600);self.assertEqual(plan.date,'');self.assertEqual(plan.time,'09:00')


if __name__=='__main__':unittest.main()
