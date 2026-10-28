from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch

from autozzz.onedragon_requirements import ApplicationRequirements, PreparationCancelled


class RequirementsTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory(prefix="autozzz-requirements-")
        self.addCleanup(self.directory.cleanup)
        self.data=Path(self.directory.name)
        self.calls=[]
        self.configs={}
        self.ctx=SimpleNamespace(team_config=SimpleNamespace(team_list=[SimpleNamespace(auto_battle="首发"),SimpleNamespace(auto_battle="另一队")]),
            battle_assistant_config=SimpleNamespace(use_merged_file=True,auto_battle_config="通用",dodge_assistant_config="闪避"),
            auto_battle_context=SimpleNamespace(_op_cache={"old":SimpleNamespace(dispose=lambda:self.calls.append("disposed"))},auto_op=None,
                init_auto_op=lambda name,directory:self.calls.append((directory,name))),
            lost_void=SimpleNamespace(init_before_run=lambda:self.calls.append("lost"),init_lost_void_det_model=lambda:self.calls.append("target")),
            withered_domain=SimpleNamespace(init_before_run=lambda:self.calls.append("withered"),map_service=SimpleNamespace(init_event_yolo=lambda:self.calls.append("event"))))
        self.requirements=ApplicationRequirements(self.ctx,self.data,SimpleNamespace(get=lambda key:self.configs[key]))

    def file(self,folder,name,suffix=".sample.yml"):
        path=self.data/"config"/folder/(name+suffix)
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text("{}",encoding="utf-8")
        return path

    def lost(self,mission="战线肃清",index=-1,priority=False):
        self.configs["lost_void"]=SimpleNamespace(mission_name=mission,challenge_config="挑战")
        self.configs["lost_void_challenge:挑战"]=SimpleNamespace(predefined_team_idx=index,auto_battle="当前队伍策略",choose_team_by_priority=priority)
        self.file("lost_void_challenge","挑战")

    def loader(self):
        return patch.dict(sys.modules,{"one_dragon.base.conditional_operation.loader":SimpleNamespace(ConditionalOperatorLoader=SimpleNamespace(
            get_yaml_file_path=lambda folders,name,merged:str(self.data/"config"/folders[0]/(name+".sample.yml"))
        ))})

    def test_defense_prepares_all_scannable_teams_without_legacy_filters(self):
        self.requirements.collect(["shiyu_defense"])
        self.assertEqual(self.requirements.policies,{("auto_battle","首发"),("auto_battle","另一队")})
        self.assertFalse(self.requirements.uses_charge)

    def test_priority_lost_void_prepares_possible_teams_and_fallback(self):
        self.lost("特遣调查",priority=True)
        self.requirements.collect(["lost_void"])
        self.assertEqual({name for _,name in self.requirements.policies},{"首发","另一队","当前队伍策略"})
        self.assertEqual(self.requirements.models,{"lost_void_det"})

    def test_matrix_uses_team_one_but_rejects_other_invalid_indices(self):
        self.lost("矩阵行动")
        self.requirements.collect(["lost_void"])
        self.assertEqual(self.requirements.policies,{("auto_battle","首发")})
        self.configs["lost_void_challenge:挑战"].predefined_team_idx=-2
        with self.assertRaisesRegex(ValueError,"队伍位置"):
            self.requirements.collect(["lost_void"])

    def test_missing_challenge_does_not_create_default_or_start_models(self):
        self.configs["withered_domain"]=SimpleNamespace(challenge_config="不存在")
        with self.assertRaisesRegex(ValueError,"挑战方案不存在"):
            self.requirements.collect(["withered_domain"])
        self.assertEqual(self.calls,[])
        self.assertFalse((self.data/"config"/"hollow_zero_challenge"/"不存在.yml").exists())

    def test_intel_uses_selected_team_policy_not_current_team_fallback(self):
        self.configs["intel_board"]=SimpleNamespace(predefined_team_idx=1,auto_battle_config="当前队伍策略")
        self.requirements.collect(["intel_board"])
        self.assertEqual(self.requirements.policies,{("auto_battle","另一队")})

    def test_load_checks_all_files_before_models_and_invalidates_policy_cache(self):
        self.lost(index=0)
        self.requirements.collect(["lost_void","shiyu_defense"])
        self.file("auto_battle","首发")
        with self.loader(),self.assertRaisesRegex(ValueError,"战斗策略不存在"):
            self.requirements.load(lambda:False,lambda label:None)
        self.assertEqual(self.calls,[])
        self.file("auto_battle","另一队")
        with self.loader():self.requirements.load(lambda:False,lambda label:None)
        self.assertEqual(self.calls[0],"lost")
        self.assertEqual(self.calls[1],"disposed")
        self.assertEqual(set(self.calls[2:]),{("auto_battle","首发"),("auto_battle","另一队")})
        self.assertEqual(self.ctx.auto_battle_context._op_cache,{})

    def test_cancel_after_model_loading_never_continues_to_policy(self):
        self.configs["intel_board"]=SimpleNamespace(predefined_team_idx=0,auto_battle_config="当前队伍策略")
        self.requirements.collect(["intel_board"])
        self.file("auto_battle","首发")
        cancelled=[]
        self.ctx.lost_void.init_lost_void_det_model=lambda:cancelled.append(True)
        with self.loader(),self.assertRaises(PreparationCancelled):
            self.requirements.load(lambda:bool(cancelled),lambda label:None)
        self.assertEqual(self.calls,[])

    def test_policy_file_cannot_resolve_outside_its_directory(self):
        self.requirements.policy("策略")
        path=self.file("other","策略")
        with patch.dict(sys.modules,{"one_dragon.base.conditional_operation.loader":SimpleNamespace(ConditionalOperatorLoader=SimpleNamespace(get_yaml_file_path=lambda *args:str(path)))}):
            with self.assertRaisesRegex(ValueError,"战斗策略不存在"):
                self.requirements.load(lambda:False,lambda label:None)

    def test_cancelled_noncombat_application_never_reports_prepared(self):
        with self.loader(),self.assertRaises(PreparationCancelled):
            self.requirements.load(lambda:True,lambda label:None)


if __name__=="__main__":unittest.main()
