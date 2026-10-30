from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from autozzz.config import Settings
from autozzz.schedules import Schedule
from autozzz.schedule_authorization import authorize,daily_review,validate_authorization


class ScheduleAuthorizationTests(unittest.TestCase):
    def native(self):
        plan=Schedule(mode='native',instance=3,app_id='email')
        review=dict(format=1,server='cn',configuration_valid=True,uses_charge=False,intent='a'*64,
                    selection=dict(instance_idx=3,app_id='email'),instances=[dict(idx=3,resources=[])])
        return plan,review

    def test_native_account_application_and_each_cycle_budget_are_bound(self):
        plan,review=self.native();plan.authorization=authorize(plan,review)
        validate_authorization(replace(plan,enabled=True))
        for changed in (replace(plan,instance=2),replace(plan,app_id='one_dragon'),replace(plan,server='global'),replace(plan,timeout=800)):
            with self.subTest(changed=changed),self.assertRaises(ValueError):validate_authorization(changed)
        validate_authorization(replace(plan,time='10:00',name='new display name'))

    def test_budget_or_resource_permission_is_never_silently_inferred(self):
        plan,review=self.native();review['uses_charge']=True
        with self.assertRaises(ValueError):authorize(plan,review)
        plan=replace(plan,allowance=20,battles=1,maximum=20)
        review['instances'][0]['resources']=[dict(id='restore',requires_confirmation=True,text='使用电池')]
        with self.assertRaises(ValueError):authorize(plan,review)
        plan.authorization=authorize(plan,review,True);validate_authorization(plan)
        with self.assertRaises(ValueError):validate_authorization(replace(plan,allowance=40,battles=2))
        self.assertEqual(plan.allowance,20)

    def test_incomplete_or_different_review_does_not_enable_execution(self):
        plan,review=self.native()
        for changes in (dict(configuration_valid=False),dict(format=True),dict(selection=None),dict(intent='bad'),dict(instances=[]),dict(instances=[dict(resources=['not an action'])])):
            with self.subTest(changes=changes),self.assertRaises(ValueError):authorize(plan,dict(review,**changes))
        rotation=replace(plan,mode='rotation',instance=None,app_id='one_dragon')
        authorize(rotation,dict(review,selection=None))

    def test_daily_review_is_saved_configuration_without_game_or_user_config_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);game=root/'ZenlessZoneZero.exe';game.touch();path=root/'config.json'
            settings=Settings(game_path=str(game));settings.save(path);before=path.read_bytes();plan=Schedule()
            review=daily_review(plan,path);self.assertEqual(path.read_bytes(),before)
            plan.authorization=authorize(plan,review);validate_authorization(replace(plan,enabled=True))
            self.assertEqual(review['settings']['game_path'],str(game));self.assertFalse(review['uses_charge'])
            settings.action_delay=2;settings.save(path)
            self.assertNotEqual(daily_review(plan,path)['config_hash'],review['config_hash'])


if __name__=='__main__':unittest.main()
