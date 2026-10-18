import ctypes as C
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
from autozzz.battle import BattlePlan, parse_script, preset_selection, selected_preset, settlement, victory, guard_combat_scene, combat_hud, BattleWorkflow, verify_roster
from autozzz.battle_vision import energy_readout, selected_stage, horizontal_arrow
from autozzz.battle_engine import BattleWatcher, BattleEnded
from autozzz.models import AutomationError, InputInterrupted, Scene, Token, Stopped
from autozzz.windows import GameWindow, INPUT
from tests.test_core import token, scene

class ScriptTests(unittest.TestCase):
    def test_user_sequence_has_exact_durations_and_no_marker_input(self):
        items=parse_script("Ah, Eh, Space - Ehh, Space - E, Ahhh |")
        self.assertEqual([(a.key,a.seconds) for a in items],[("a",1.5),("e",1.5),("space",.08),("e",3),("space",.08),("e",.08),("a",4.5)])
    def test_custom_unit_and_lowercase(self):
        self.assertEqual(parse_script("ahh, e, space |",.7)[0].seconds,1.4)
    def test_invalid_actions_markers_and_durations_rejected(self):
        for text in ("", "W", "A;E", "Spaceh", "- E", "A - E", "Space -- E", "A | E", "Ehhhhhhhhhhh"):
            with self.subTest(text=text),self.assertRaises(ValueError):parse_script(text)
    def test_invalid_units_rejected(self):
        for unit in (True,0,4,float("nan"),float("inf")):
            with self.assertRaises(ValueError):parse_script("Ah",unit)
    def test_plan_does_not_silently_authorize_multiple_battles(self):
        plan=BattlePlan();plan.validate();self.assertEqual(plan.cycles,1);self.assertEqual(plan.max_energy,20)
        for name,value in (("cycles",0),("cycles",True),("max_energy",0),("timeout",float("nan")),("category","特训目标"),("team","")):
            with self.subTest(name=name),self.assertRaises(ValueError):
                p=BattlePlan();setattr(p,name,value);p.validate()
    def test_independent_plan_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"battle.json";p=BattlePlan();p.save(path)
            self.assertEqual(BattlePlan.load(path),p)

class BoundSelectionTests(unittest.TestCase):
    def test_two_agent_roster_and_optional_order_are_not_tied_to_server(self):
        s=scene();s.tokens=[token("蕾米埃尔",.25,.84),token("爱芮",.61,.84)]
        self.assertEqual(verify_roster(s,"蕾米埃尔，爱芮"),["蕾米埃尔","爱芮"])
        with self.assertRaises(AutomationError):verify_roster(s,"蕾米埃尔,维琳娜,爱芮")
        with self.assertRaises(AutomationError):verify_roster(s,"爱芮,蕾米埃尔")
        s.tokens.append(token("爱芮",.4,.84))
        with self.assertRaises(AutomationError):verify_roster(s)

class StrategyTests(unittest.TestCase):
    def test_builtin_global_is_preserved_and_cn_omits_middle_segment(self):
        from autozzz.battle_strategy import resolve_strategy
        root=Path(__file__).resolve().parents[1]
        p,meta=resolve_strategy(BattlePlan(strategy_file="builtin:bad-women-global"),root=root,resource_root=root)
        self.assertEqual(p.script,"Ah, Eh, Space - Ehh, Space - E, Ahhh |")
        self.assertEqual(p.expected_roster,"蕾米埃尔,维琳娜,爱芮")
        self.assertEqual(len(meta["sha256"]),64)
        p,_=resolve_strategy(BattlePlan(strategy_file="builtin:cn-team1"),root=root,resource_root=root)
        self.assertEqual(p.team,"编队1")
        self.assertEqual([(a.key,a.seconds) for a in parse_script(p.script)][:5],[("a",1.5),("e",1.5),("space",.08),("e",.08),("a",4.5)])
        self.assertEqual(sum(a.key=="space" for a in parse_script(p.script)),1)
    def test_custom_relative_path_is_frozen_and_preserves_spending_limits(self):
        from autozzz.battle_strategy import resolve_strategy
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);path=root/"custom.json"
            path.write_text(json.dumps(dict(version=1,name="自定义",team="我的队",script="Ehh |")),encoding="utf-8")
            plan=BattlePlan(strategy_file="custom.json",energy_budget=40,battles=2)
            effective,metadata=resolve_strategy(plan,root=root,resource_root=root)
            path.write_text('{}')
            self.assertEqual(effective.script,"Ehh |")
            self.assertEqual(effective.energy_budget,40);self.assertEqual(effective.battles,2)
            self.assertEqual(plan.script,BattlePlan().script)
    def test_strategy_cannot_authorize_energy_or_execute_unknown_actions(self):
        from autozzz.battle_strategy import resolve_strategy
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            for extra in (dict(energy_budget=300),dict(version=True),dict(script="W"),dict(script=""),dict(hold_unit=float("nan"))):
                data=dict(version=1,name="test",script="A");data.update(extra)
                (root/"bad.json").write_text(json.dumps(data),encoding="utf-8")
                with self.subTest(extra=extra),self.assertRaises(ValueError):
                    resolve_strategy(BattlePlan(strategy_file="bad.json"),root=root,resource_root=root)
    def test_resume_may_change_inputs_but_never_stage_team_or_budget(self):
        from dataclasses import asdict
        from autozzz.battle import same_battle_plan
        original=asdict(BattlePlan())
        modified=dict(original,script="Ahhh, E |",strategy_file="recovery.json")
        self.assertTrue(same_battle_plan(original,modified))
        for field,value in (("stage","强攻演练"),("team","其他队"),("energy_budget",300),("expected_roster","其他角色")):
            with self.subTest(field=field):self.assertFalse(same_battle_plan(original,dict(modified,**{field:value})))

class MoreSelectionTests(unittest.TestCase):
    def test_training_sidebar_native_read_fills_only_exact_missing_category(self):
        from autozzz.battle import CATEGORIES
        s=scene();s.image=np.zeros((2160,3840,3),np.uint8)
        s.tokens=[token(n,.15,.45+i*.10) for i,n in enumerate(CATEGORIES[:3])]+[token("恶名特猎",.15,.75)]
        local=scene();local.tokens=[token("恶名狩猎",.5,.85)]
        c=Mock();c.report.battle={};c.vision.read.return_value=local
        workflow=BattleWorkflow(c,BattlePlan())
        self.assertTrue(workflow.categories_ready(s))
        self.assertTrue(s.has("恶名狩猎",(.05,.25,.25,.90),exact=True))
        c.vision.read.assert_called_once()

    def test_result_completion_uses_exact_native_text_when_pointer_was_read_as_v(self):
        s=scene();s.tokens=[token("挑战结果",.5,.2),token("完成V",.885,.95)]
        s.image=np.zeros((2160,3840,3),np.uint8)
        local=scene();local.tokens=[Token("完成",.95,(.2,.2,.8,.8))]
        c=Mock();c.report.battle={};c.vision.read.return_value=local
        workflow=BattleWorkflow(c,BattlePlan())
        target=workflow.complete_button(s)
        self.assertEqual(target.text,"完成")
        self.assertIn(target,s.tokens)
        self.assertIs(workflow.complete_button(s),target)
        c.vision.read.assert_called_once()

    def test_local_start_reading_requires_exact_text_and_maps_to_same_screenshot(self):
        from autozzz.battle_vision import local_start_button
        s=scene();s.image=np.zeros((2160,3840,3),np.uint8)
        local=scene();local.tokens=[Token("出战",.95,(.2,.2,.8,.8))]
        vision=Mock();vision.read.return_value=local
        t=local_start_button(s,vision)
        self.assertTrue(t.within((.84,.91,.97,.99)))
        local.tokens=[token("出线")];self.assertIsNone(local_start_button(s,vision))
        local.tokens=[token("出战"),token("出战")];self.assertIsNone(local_start_button(s,vision))

    def test_select_must_belong_to_exact_named_card(self):
        s=scene();s.tokens=[token("坏女人",.11,.40),token("EVE",.55,.40),token("+ SELECT",.45,.53),token("+SELECT",.89,.53)]
        self.assertIs(preset_selection(s,"坏女人"),s.tokens[2])
        self.assertIsNone(preset_selection(s,"女人"))
        s.tokens.append(token("坏女人",.55,.70));self.assertIsNone(preset_selection(s,"坏女人"))
    def test_selected_marker_must_be_on_the_named_card(self):
        s=scene();s.tokens=[token("坏女人",.11,.40),token("VVA.Jr",.11,.14),token("SELECTED",.45,.27)]
        self.assertFalse(selected_preset(s,"坏女人"))
        s.tokens[-1]=token("SELECTED",.45,.53)
        self.assertTrue(selected_preset(s,"坏女人"))

    def test_no_wrong_row_or_ambiguous_select(self):
        s=scene();s.tokens=[token("坏女人",.11,.40),token("+SELECT",.45,.82)]
        self.assertIsNone(preset_selection(s,"坏女人"))
        s.tokens=[token("坏女人",.11,.40),token("+SELECT",.45,.53),token("+SELECT",.46,.54)]
        self.assertIsNone(preset_selection(s,"坏女人"))
    def test_reserve_and_battery_use_are_blocked_but_reward_item_is_not(self):
        for texts in (("备用电力","提取"),("以太电池","使用"),("使用备用电",),("电量不足",)):
            with self.subTest(texts=texts),self.assertRaises(AutomationError):guard_combat_scene(scene(*texts))
        guard_combat_scene(scene("获得","以太电池","确认"))

    def test_energy_bound_to_next_button_area_and_unique(self):
        s=scene();s.tokens=[token("320/20",.80,.95),token("320/240",.73,.06)]
        self.assertEqual(energy_readout(s),(320,20))
        s.tokens.append(token("300/40",.81,.96));self.assertIsNone(energy_readout(s))
    def test_stage_highlight_and_horizontal_arrow_use_pixels(self):
        import cv2
        s=scene();s.image=np.zeros((1080,1920,3),dtype=np.uint8);s.tokens=[token("异常演练",.61,.70)]
        self.assertFalse(selected_stage(s,"异常演练"))
        cv2.rectangle(s.image,(1090,700),(1400,845),(0,205,160),4)
        self.assertTrue(selected_stage(s,"异常演练"))
        pts=np.array([[1810,754],[1824,776],[1810,798],[1817,776]],np.int32)
        cv2.fillPoly(s.image,[pts],(255,255,255));self.assertIsNotNone(horizontal_arrow(s,"right"))
    def test_earned_overlay_uses_confirmation_not_item_type_or_go_button(self):
        s=scene();s.tokens=[token("获得",.5,.39),token("任意奖励",.5,.57),token("前往",.43,.675),token("确认",.59,.675)]
        self.assertIs(BattleWorkflow.earned_reward(s),s.tokens[-1])
        s.tokens.append(token("确认",.56,.68));self.assertIsNone(BattleWorkflow.earned_reward(s))

    def test_result_button_ocr_miss_waits_for_battle_page_not_daily_entry(self):
        early=scene();early.tokens=[token("挑战结果",.5,.20)]
        result=scene();result.tokens=[token("挑战结果",.5,.20),token("完成",.885,.95)]
        challenge=scene();challenge.tokens=[token("下一步",.90,.95),token("挑战等级",.75,.05),token("异常演练",.3,.70),token("支援演练",.5,.70),token("防护演练",.7,.70)]
        c=Mock();c.report.battle={};c.snapshot.side_effect=[early,result,challenge];c.settings.transition_timeout=45;c.settings.action_delay=1.2
        daily=Mock()
        with patch("autozzz.workflows.DailyWorkflow",return_value=daily):
            self.assertIs(BattleWorkflow(c,BattlePlan()).training(),challenge)
        daily.ensure_main.assert_not_called()
        self.assertEqual(c.wait.call_count,2)
        c.window.click.assert_called_once()

    def test_squad_and_material_menu_real_labels(self):
        s=scene();s.tokens=[token("预备编队",.24,.95),token("战斗设置",.12,.95)]
        self.assertTrue(BattleWorkflow.squad(s))
        s.tokens=[token(t,.15+i*.20,.77) for i,t in enumerate(("基础材料","代理人晋升","音擎改装","代理人技能"))]
        self.assertTrue(BattleWorkflow.material_menu(s))
    def test_settlement_large_s_or_result_never_combat_hud(self):
        s=scene();s.tokens=[token("E",.90,.94),token("Q",.82,.94)]
        self.assertTrue(combat_hud(s));self.assertFalse(settlement(s))
        s.tokens.append(Token("S",.95,(.20,.20,.40,.50)))
        self.assertTrue(settlement(s));self.assertFalse(combat_hud(s))
    def test_result_success_requires_the_completed_objective_on_its_own_row(self):
        s=scene();s.tokens=[token("挑战结果",.5,.20),token("完成实战模拟",.15,.64),token("0/1",.30,.64),token("1/1",.30,.69)]
        self.assertFalse(victory(s))
        s.tokens[2]=token("1/1",.30,.64);self.assertTrue(victory(s))

    def test_animated_header_duplicate_or_missing_header_uses_bound_result_controls(self):
        s=scene();s.tokens=[token("挑战战结果",.5,.20),token("完成实战模拟",.15,.64),token("1/1",.30,.64)]
        self.assertTrue(settlement(s));self.assertTrue(victory(s))
        s.tokens=s.tokens[1:]+[token("再来一次",.71,.95),token("完成",.88,.95)]
        self.assertTrue(settlement(s));self.assertTrue(victory(s))

    def test_small_s_in_hud_not_a_result(self):
        s=scene();s.tokens=[token("S",.5,.5)]
        self.assertFalse(settlement(s))

class BudgetTests(unittest.TestCase):
    def test_reservations_survive_restart_and_block_a_second_pending_battle(self):
        from autozzz.battle_budget import BattleBudget
        with tempfile.TemporaryDirectory() as d:
            b=BattleBudget(d,"session","global",300,15)
            item=b.reserve(20,"test")
            b=BattleBudget(d,"session","global",300,15)
            self.assertEqual(b.remaining,280)
            with self.assertRaises(AutomationError):b.reserve(20,"repeat")
            b.settle(item,280)
            for _ in range(14):
                item=b.reserve(20,"test");b.settle(item,0)
            self.assertEqual(b.remaining,0)
            with self.assertRaises(AutomationError):b.reserve(20,"sixteenth")
    def test_new_budget_id_cannot_bypass_an_unsettled_battle(self):
        from autozzz.battle_budget import BattleBudget
        with tempfile.TemporaryDirectory() as d:
            b=BattleBudget(d,"first","global",20,1);b.reserve(20,"test")
            with self.assertRaises(AutomationError):BattleBudget(d,"newid","global",300,15)

    def test_existing_budget_cannot_be_reset_or_increased(self):
        from autozzz.battle_budget import BattleBudget
        with tempfile.TemporaryDirectory() as d:
            BattleBudget(d,"session","global",20,1)
            with self.assertRaises(AutomationError):BattleBudget(d,"session","global",300,15)
    def test_real_hud_works_without_tiny_e_and_q_but_requires_hp_bar(self):
        s=scene();s.tokens=[token("11000/11028",.15,.085),token("Space",.925,.955)]
        s.image=np.zeros((1080,1920,3),np.uint8)
        self.assertFalse(combat_hud(s))
        s.image[50:70,270:380]=(0,255,100)
        self.assertTrue(combat_hud(s))
        s.tokens[0]=token("0/11028",.15,.085)
        self.assertFalse(combat_hud(s))

class EngineTests(unittest.TestCase):
    def test_early_s_result_waits_read_only_for_completed_objective_and_energy(self):
        from autozzz.battle_engine import finish_settlement
        c=Mock();c.report.battle=dict(energy_before=81,energy_cost=20)
        early=scene();early.tokens=[token("挑战结果",.5,.20)]
        full=scene();full.tokens=[token("挑战结果",.5,.20),token("完成实战模拟",.15,.64),token("1/1",.30,.64),token("61/20",.59,.95)]
        c.wait.side_effect=lambda predicate,*a,**k: full if predicate(full) else self.fail("missing full-result proof")
        finish_settlement(c,early)
        c.wait.assert_called_once();c.key.assert_not_called();c.window.combat_press.assert_not_called()
        self.assertEqual(c.report.battle["energy_after"],61)

    def test_settlement_during_first_hold_releases_and_never_starts_next_action(self):
        from autozzz.battle_engine import execute_battle
        c=Mock();c.runner.inactive_duration=0;c.runner._stop.is_set.return_value=False
        c.settings.transition_timeout=45;c.runner.input_epoch=0
        c.report.battle=dict(energy_before=300,energy_cost=20)
        result=scene();result.tokens=[token("挑战结果",.5,.20),token("完成实战模拟",.15,.64),token("1/1",.30,.64),token("280/20",.59,.95)]
        watch=Mock();watch.finished=threading.Event();watch.ready=threading.Event();watch.ready.set()
        watch.epoch=0;watch.error=None;watch.scene=result
        def press(action,seconds,checkpoint):
            watch.finished.set();raise BattleEnded()
        c.window.combat_press.side_effect=press
        with patch("autozzz.battle_engine.BattleWatcher",return_value=watch):execute_battle(c,BattlePlan())
        c.window.combat_press.assert_called_once()
        self.assertTrue(c.report.battle["completed"])
        self.assertEqual(c.report.stamina_spent,20)
        self.assertEqual(c.report.battle["actions"][0]["status"],"settlement_released")
        c.key.assert_not_called()

class InputReleaseTests(unittest.TestCase):
    def test_attack_is_mouse_down_not_keyboard_a_and_always_releases(self):
        window=Mock(spec=GameWindow)
        for key in ("a","e","space"):
            checkpoint=Mock(side_effect=[None,Stopped("F10")])
            with patch("autozzz.windows.user32.SendInput",return_value=1) as send,patch("autozzz.windows.user32.MapVirtualKeyW",return_value=1):
                with self.assertRaises(Stopped):GameWindow.combat_press(window,key,1.5,checkpoint)
                event=window.send.call_args.args[0][0]
                self.assertEqual(event.type,0 if key=="a" else 1)
                up=C.cast(send.call_args.args[1],C.POINTER(INPUT)).contents
                self.assertEqual(up.mi.dwFlags if key=="a" else up.ki.dwFlags,4 if key=="a" else 2)
            window.reset_mock()
    def test_down_failure_still_sends_release(self):
        window=Mock(spec=GameWindow);window.send.side_effect=AutomationError("down failed")
        with patch("autozzz.windows.user32.SendInput",return_value=1) as send:
            with self.assertRaises(AutomationError):GameWindow.combat_press(window,"a",1,Mock())
            send.assert_called_once()
    def test_result_flag_interrupts_a_hold_before_another_action(self):
        context=Mock();watch=BattleWatcher(context,6)
        watch.finished.set()
        with self.assertRaises(BattleEnded):watch.input_guard()
        context.input_checkpoint.assert_not_called()
    def test_stale_or_not_ready_hud_never_authorizes_input(self):
        context=Mock();watch=BattleWatcher(context,6)
        for state,age in (("loading",0),("combat",10)):
            watch.state=state;watch.ready.set();watch.updated=time.monotonic()-age
            with self.assertRaises(InputInterrupted):watch.input_guard()

if __name__=="__main__":unittest.main()
