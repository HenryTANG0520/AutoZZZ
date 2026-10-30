"""Opt-in training navigation and bounded, interruptible combat scripts."""
from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from .models import AutomationError, InputInterrupted, Scene, Stopped, TaskResult, Token, normalize
from .policy import guard_scene

CATEGORIES = ("实战模拟室", "区域巡防", "专业挑战室", "恶名狩猎")
DEFAULT_SCRIPT = "Ah, Eh, Space - Ehh, Space - E, Ahhh |"

@dataclass(frozen=True)
class Action:
    key: str
    seconds: float
    switch: bool = False


def parse_script(text: str, unit: float = 1.5) -> list[Action]:
    if not isinstance(text, str) or not text.strip() or len(text) > 2000:
        raise ValueError("请输入不超过2000字的战斗脚本。")
    if type(unit) not in (int, float) or not math.isfinite(unit) or not .2 <= unit <= 3:
        raise ValueError("长按单位需为0.2至3秒。")
    # A is the normal attack action (mouse left), never the movement key A.
    words = re.findall(r"Spaceh*|[AEQ]h*|[-|]|[^\s,，]+", text, re.I)
    result = []
    for i, word in enumerate(words):
        if word == "|":
            if i != len(words)-1:
                raise ValueError("| 仅可放在一轮脚本末尾。")
            continue
        if word == "-":
            if i == 0 or not re.fullmatch(r"Space", words[i-1], re.I):
                raise ValueError("换人标记 - 必须紧跟 Space；标记本身不发送按键。")
            result[-1] = Action("space", result[-1].seconds, True)
            continue
        match = re.fullmatch(r"(Space|A|E|Q)(h*)", word, re.I)
        if not match:
            raise ValueError(f"无法识别脚本：{word}；支持 A、E、Q、Space、h、逗号、- 和末尾 |。")
        key, holds = match.groups()
        duration = len(holds) * unit if holds else .08
        if duration > 15 or (key.casefold() == "space" and holds):
            raise ValueError("单次长按不能超过15秒；Space仅支持短按。")
        result.append(Action(key.casefold(), duration, key.casefold() == "space"))
    if not result or len(result) > 200:
        raise ValueError("脚本需包含1至200个动作。")
    return result

@dataclass
class BattlePlan:
    category: str = "实战模拟室"
    subcategory: str = "代理人晋升"
    stage: str = "异常演练"
    team: str = "坏女人"
    script: str = DEFAULT_SCRIPT
    hold_unit: float = 1.5
    intro_wait: float = 6.0
    timeout: float = 180.0
    cycles: int = 1
    max_energy: int = 20
    battles: int = 1
    energy_budget: int = 20
    budget_id: str = ""
    expected_roster: str = ""
    strategy_file: str = ""

    def validate(self):
        if self.category not in CATEGORIES:
            raise ValueError("请选择训练中的四个副本分类之一。")
        for name in ("subcategory", "stage", "team"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or len(value) > 50:
                raise ValueError("副本分类、目标副本及预编队伍名称不能为空且不能超过50字。")
            setattr(self, name, value.strip())
        for name, low, high in (("intro_wait", 0, 30), ("timeout", 20, 600)):
            v = getattr(self, name)
            if type(v) not in (int, float) or not math.isfinite(v) or not low <= v <= high:
                raise ValueError(f"{name} 超出范围（{low}–{high}秒）。")
        if type(self.cycles) is not int or not 1 <= self.cycles <= 12:
            raise ValueError("脚本轮数必须是1至12；每次仅挑战一次副本。")
        if type(self.max_energy) is not int or not 20 <= self.max_energy <= 100:
            raise ValueError("单次电量上限需为20至100。")
        if type(self.battles) is not int or self.battles<1:
            raise ValueError("批次场次须为正整数。")
        if type(self.energy_budget) is not int or self.energy_budget<20:
            raise ValueError("批次总电量须为不小于20的整数。")
        if self.budget_id and not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}",self.budget_id):
            raise ValueError("预算编号格式不正确。")
        if not isinstance(self.expected_roster,str):
            raise ValueError("核对角色请按出场顺序用逗号分隔。")
        if self.expected_roster and not 1<=len(roster_names(self.expected_roster))<=3:
            raise ValueError("核对角色需包含1至3个名字。")
        if not isinstance(self.strategy_file,str) or len(self.strategy_file)>1024:
            raise ValueError("策略文件路径格式不正确。")
        parse_script(self.script, self.hold_unit)

    @classmethod
    def load(cls, path: Path):
        plan = cls(**json.loads(path.read_text(encoding="utf-8-sig"))) if path.exists() else cls()
        plan.validate()
        return plan

    def save(self, path: Path):
        self.validate()
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)


def roster_names(text):
    return [name.strip() for name in re.split(r"[,，、]",text) if name.strip()]


def verify_roster(scene, expected=""):
    roster=sorted([t for t in scene.tokens if t.score>=.6 and t.within((.08,.815,.80,.867))
                   and not normalize(t.text).startswith("等级")],key=lambda t:t.center[0])
    names=[t.text for t in roster]
    if not 1<=len(names)<=3 or len({normalize(n) for n in names})!=len(names):
        raise AutomationError("应用预编后未唯一核实1至3名角色，禁止出战。")
    if expected and [normalize(n) for n in names]!=[normalize(n) for n in roster_names(expected)]:
        raise AutomationError(f"应用队伍的角色或顺序与方案不符：{names}；禁止出战。")
    return names


def guard_combat_scene(scene):
    guard_scene(scene)
    region=(.20,.15,.85,.86)
    if scene.has(("使用备用电","提取备用电","消耗备用电","使用以太电池","购买电量","补充电量")):
        raise AutomationError("检测到电量补充入口，禁止使用备用电、电池或购买电量。")
    if (scene.has(("备用电","以太电池"),region)
            and scene.has(("使用","提取","消耗","补充"),region,exact=True)):
        raise AutomationError("检测到电量补充选项，战斗批次已停止。")


def unique(scene: Scene, text: str, area=(0,0,1,1), *, contains=False):
    matches = [t for t in scene.tokens if t.score >= .6 and t.within(area)
               and (normalize(text) in normalize(t.text) if contains else normalize(t.text) == normalize(text))]
    return matches[0] if len(matches) == 1 else None


def preset_selection(scene: Scene, name: str):
    names = [t for t in scene.tokens if normalize(t.text) == normalize(name) and t.within((.06,.09,.92,.87))]
    if len(names) != 1:
        return None
    title = names[0]
    x,y = title.center
    # Two-column cards; SELECT must be on this exact card, below its title.
    area = (.40 if x < .5 else .82, y + .025, .49 if x < .5 else .94, min(.92,y+.23))
    buttons = [t for t in scene.tokens if t.within(area) and normalize(t.text).lstrip("+") == "select"]
    return buttons[0] if len(buttons) == 1 else None


def selected_preset(scene: Scene, name: str):
    titles=[t for t in scene.tokens if normalize(t.text)==normalize(name) and t.within((.06,.09,.92,.87))]
    if len(titles)!=1:return False
    x,y=titles[0].center
    area=(.40 if x<.5 else .82,y+.025,.49 if x<.5 else .94,min(.92,y+.23))
    return unique(scene,"SELECTED",area) is not None


def result_header(scene):
    # The animated 3D shadow has been read as a duplicated 战 on real results.
    titles=[t for t in scene.tokens if t.within((.25,.08,.70,.31))
            and normalize(t.text) in ("挑战结果","挑战战结果","战斗结算")]
    return len(titles)==1

def result_footer(scene):
    return bool(unique(scene,"再来一次",(.62,.90,.81,1)) and unique(scene,"完成",(.80,.90,1,1)))


def settlement(scene: Scene) -> bool:
    explicit = scene.has(("挑战成功", "战斗胜利", "战斗结束", "委托完成"), (.05,.05,.95,.85))
    rank = [t for t in scene.tokens if normalize(t.text) in ("s", "ss", "sss")
            and t.within((.05,.08,.85,.78)) and t.box[3]-t.box[1] >= .065]
    result = scene.has(("挑战结果", "战斗结算", "结算", "完成挑战", "再次挑战", "挑战用时"))
    return bool(explicit or rank or result or result_header(scene) or result_footer(scene))


def victory(scene: Scene) -> bool:
    if scene.has(("挑战成功","战斗胜利","委托完成")):
        return True
    label=unique(scene,"完成实战模拟",(.03,.45,.29,.85))
    if not label or not (result_header(scene) or result_footer(scene)):return False
    y=label.center[1]
    return unique(scene,"1/1",(.27,y-.018,.33,y+.018)) is not None


def combat_hud(scene: Scene) -> bool:
    if settlement(scene) or scene.has(("预备出战","下一步","预编配队","预备编队","购买电量","电量不足","退出战斗")):
        return False
    hints=all(scene.has(key,(.65,.60,1,1),exact=True) for key in ("E","Q"))
    hp=[]
    for t in scene.tokens:
        if t.within((.10,.05,.24,.13)):
            m=re.fullmatch(r"(\d{1,7})[/／](\d{1,7})",normalize(t.text))
            if m and 0<int(m[1])<=int(m[2]):hp.append(m)
    # Whole-screen OCR misses the tiny E/Q black keycaps. Bind Space to the
    # combat HP readout and its green bar; world and setup have neither pair.
    if not (hints or (len(hp)==1 and unique(scene,"Space",(.88,.90,.98,.99)))):
        return False
    if scene.image is None:return hints
    import numpy as np
    h,w=scene.image.shape[:2]
    patch=scene.image[round(.035*h):round(.09*h),round(.11*w):round(.50*w)]
    b,g,r=[patch[:,:,i].astype(int) for i in range(3)]
    return bool(np.count_nonzero((g>175)&(b<100)&(g-r>25))>.00015*w*h)


def same_battle_plan(previous, current):
    # Recovery may change bounded inputs, never the server, stage, team or budget.
    execution={"script","hold_unit","intro_wait","timeout","cycles","strategy_file"}
    return {k:v for k,v in previous.items() if k not in execution}=={k:v for k,v in current.items() if k not in execution}


class BattleWorkflow:
    def __init__(self, context, plan: BattlePlan):
        self.c, self.plan = context, plan
        self.submitted = False
        self.details = self.c.report.battle
        self.budget = None

    def snap(self):
        scene = self.c.snapshot()
        guard_combat_scene(scene)
        return scene

    def complete_button(self,scene):
        target=unique(scene,"完成",(.78,.90,1,1))
        if not target and settlement(scene):
            from .battle_vision import local_button
            target=local_button(scene,self.c.vision,"完成")
            if target:scene.tokens.append(target)
        return target

    def categories_ready(self,scene):
        area=(.05,.25,.25,.90)
        if all(unique(scene,n,area) for n in CATEGORIES):return True
        if sum(bool(unique(scene,n,area)) for n in CATEGORIES)<3:return False
        from .battle_vision import regional_tokens
        tokens=regional_tokens(scene,self.c.vision,(.08,.40,.23,.82))
        for name in CATEGORIES:
            matches=[t for t in tokens if normalize(t.text)==normalize(name) and t.score>=.6]
            if not unique(scene,name,area) and len(matches)==1:scene.tokens.append(matches[0])
        return all(unique(scene,n,area) for n in CATEGORIES)

    def click(self, scene, target):
        if target is None:
            raise AutomationError("战斗页面没有唯一匹配的目标；未继续点击。")
        # Combat uses its own opt-in policy, without broadening daily click policy.
        guard_combat_scene(scene)
        self.c.checkpoint()
        self.c._input_scene(scene, "战斗页面已过期，请重新识别。")
        self.c.log(f"战斗点击：{target.text if isinstance(target, Token) else target}")
        try:
            self.c.window.click(*(target.center if isinstance(target,Token) else target), scene.geometry)
        except Exception:
            self.c.last_scene = None
            raise
        self.c.last_scene = None
        self.c.sleep(self.c.settings.action_delay)

    @staticmethod
    def squad(scene):
        return bool(scene.has(("预备编队","预编配队"),(.10,.87,.60,1)) and unique(scene,"战斗设置",(.02,.87,.21,1)))

    @staticmethod
    def presets(scene):
        return sum(normalize(t.text).lstrip("+") == "select" for t in scene.tokens) >= 2 and not scene.has("下一步")

    @staticmethod
    def challenge(scene):
        return scene.has("下一步",(.75,.88,1,1)) and scene.has(("挑战等级", "挑战奖励"))

    @staticmethod
    def material_menu(scene):
        return all(scene.has(t,(.03,.55,.96,.92),exact=True) for t in ("基础材料","代理人晋升","音擎改装","代理人技能"))

    @staticmethod
    def earned_reward(scene):
        if unique(scene,"获得",(.36,.30,.65,.45)):
            return unique(scene,"确认",(.35,.60,.68,.73))
        return None

    def training(self):
        from .workflows import DailyWorkflow, TABS
        daily = DailyWorkflow(self.c)
        for _ in range(5):
            s = self.snap()
            reward=self.earned_reward(s)
            if reward:
                self.c.evidence(s,"battle_earned_reward")
                self.click(s,reward)
                self.c.wait(lambda f:self.challenge(f) or self.earned_reward(f) or self.squad(f) or self.material_menu(f),
                            self.c.settings.transition_timeout,"奖励弹窗关闭后的副本页",allow_blank=True)
                continue
            if settlement(s) and not self.complete_button(s):
                self.c.wait(lambda f:settlement(f) and self.complete_button(f),self.c.settings.transition_timeout,"结算完成按钮稳定",allow_blank=True)
                continue
            if settlement(s) and self.complete_button(s):
                self.click(s,self.complete_button(s))
                self.c.wait(lambda f:self.challenge(f) or self.earned_reward(f) or self.squad(f) or self.material_menu(f) or daily._handbook(f) or daily._main(f),
                            self.c.settings.transition_timeout,"结算返回后的已知页面",allow_blank=True)
                continue
            if self.challenge(s) and self.plan.category=="实战模拟室" and self.plan.subcategory=="代理人晋升" and sum(s.has(t,(.03,.63,.97,.85)) for t in ("强攻演练","击破演练","异常演练","支援演练","防护演练","命破演练","锋御演练"))>=3:
                return s
            if s.has("保存方案",(.75,.90,1,1)) and s.has("电量消耗"):
                self.c.key("esc",s)
                continue
            if s.has("模板重命名") and s.has("请输入新的模板名称"):
                self.c.key("esc",s)
                continue
            if self.material_menu(s) and self.plan.category == "实战模拟室":
                return s
            if daily._handbook(s):
                break
            if self.presets(s) or self.squad(s) or self.challenge(s):
                self.c.key("esc",s)
                continue
            if (daily._main(s) or daily._start_button(s) or daily._cached_login(s)
                    or daily._monthly_card_visible(s) or daily._entry_network_error_visible(s)):
                daily.ensure_main()
                daily._open_daily()
            else:
                self.c.evidence(s,"battle_navigation_transition")
                self.c.wait(lambda f:settlement(f) or self.earned_reward(f) or self.squad(f) or self.challenge(f)
                            or self.material_menu(f) or daily._handbook(f) or daily._entry_ready(f),
                            self.c.settings.transition_timeout,"战斗导航的已知页面",allow_blank=True)
        else:
            raise AutomationError("无法从当前副本配置页返回训练手册。")
        s = self.snap()
        self.click(s,unique(s,"训练",TABS))
        return self.c.wait(lambda x:x.has("实战模拟室",(.05,.25,.25,.90)),
                           self.c.settings.transition_timeout,"训练分类")

    def open_subcategory(self, s):
        if self.challenge(s):return s
        if self.material_menu(s):
            self.click(s,unique(s,self.plan.subcategory,(.03,.55,.96,.92)))
            return self.c.wait(self.challenge,self.c.settings.transition_timeout,"模拟室演练选择",allow_blank=True)
        self.categories_ready(s)
        self.click(s,unique(s,self.plan.category,(.06,.25,.25,.88)))
        self.c.sleep(.4)
        seen = set()
        for _ in range(12):
            s=self.snap()
            self.c.evidence(s,"battle_training_list")
            title=unique(s,self.plan.subcategory,(.25,.23,.85,.86))
            if title:
                y=title.center[1]
                buttons=[t for t in s.tokens if normalize(t.text)=="前往" and t.within((.66,y,.87,min(.90,y+.19)))]
                if len(buttons)!=1:
                    raise AutomationError("副本标题附近没有唯一前往按钮。")
                self.click(s,buttons[0])
                return self.c.wait(self.challenge,self.c.settings.transition_timeout,"训练副本选择",allow_blank=True)
            signature=tuple((normalize(t.text),round(t.center[1],2)) for t in s.tokens if t.within((.25,.24,.90,.88)))
            if signature in seen: break
            seen.add(signature)
            self.c.move(s,(.68,.70)); self.c.scroll(-360)
        raise AutomationError(f"当前账号训练列表中没有找到：{self.plan.subcategory}。")

    def select_stage(self, s):
        seen=set()
        for _ in range(12):
            s=self.snap()
            target=unique(s,self.plan.stage,(.035,.63,.95,.84))
            self.c.evidence(s,"battle_stage_list")
            if target:
                from .battle_vision import selected_stage
                if selected_stage(s,self.plan.stage):
                    return s
                # Header text doubles as the rename control on selected cards.
                self.click(s,(target.center[0]+.02,.765))
                return self.snap()
            sig=tuple(normalize(t.text) for t in s.tokens if t.within((.03,.63,.97,.85)))
            if sig in seen: break
            seen.add(sig)
            # The visible horizontal arrow is verified by its bright connected pixels.
            from .battle_vision import horizontal_arrow
            arrow=horizontal_arrow(s, "right")
            if arrow is None: break
            self.click(s,arrow)
        raise AutomationError(f"有限横向翻页后仍未找到副本：{self.plan.stage}。")

    def apply_preset(self,s):
        if not selected_preset(s,self.plan.team):
            raise AutomationError("预编卡片的指定队名与SELECTED没有同时核实。")
        self.c.evidence(s,"battle_preset_selected")
        # This button applies the preset; the returned squad has a separate 出战.
        self.click(s,unique(s,"预备出战",(.72,.90,1,1)))
        s=self.c.wait(self.squad,self.c.settings.transition_timeout,"应用预编队伍后的配队页")
        self.c.move(s,(.5,.05));s=self.snap()
        names=verify_roster(s,self.plan.expected_roster)
        self.details.update(team_verified=True,selected_roster=names,team_selection_basis="唯一队名所在卡片SELECTED，应用后读取角色及顺序，与方案的可选角色名单核对")
        self.c.evidence(s,"battle_team_selected")
        return s

    def select_team(self, s):
        self.click(s,unique(s,"预备编队",(.10,.87,.60,1)) or unique(s,"预编配队",(.10,.87,.60,1)))
        self.c.wait(self.presets,self.c.settings.transition_timeout,"预编队伍列表")
        s=self.snap(); self.c.move(s,(.77,.53)); self.c.scroll(360)
        seen=set()
        for _ in range(12):
            s=self.snap()
            self.c.evidence(s,"battle_presets")
            if selected_preset(s,self.plan.team):return self.apply_preset(s)
            target=preset_selection(s,self.plan.team)
            if target:
                self.click(s,target)
                s=self.c.wait(lambda f:selected_preset(f,self.plan.team),self.c.settings.transition_timeout,"指定队伍SELECTED")
                return self.apply_preset(s)
            if sum(normalize(t.text)==normalize(self.plan.team) for t in s.tokens)>1:
                raise AutomationError("预编队伍同名，无法唯一选择；请在游戏内改为唯一名字。")
            sig=tuple(normalize(t.text) for t in s.tokens)
            if sig in seen: break
            seen.add(sig)
            self.c.move(s,(.77,.53));self.c.scroll(-360)
        raise AutomationError(f"没有找到预编队伍 {self.plan.team}；请提前在游戏内配好并命名，不会回退其他队。")

    def minimum_configuration(self,s):
        from .battle_vision import enemy_slots,energy_readout
        self.click(s,(.85,.40))
        s=self.c.wait(lambda f:f.has("保存方案",(.75,.90,1,1)) and f.has("电量消耗"),
                      self.c.settings.transition_timeout,"模拟室怪物配置")
        for _ in range(5):
            self.c.evidence(s,"battle_minimum_config")
            slots=enemy_slots(s)
            energy=energy_readout(s)
            if not energy or not slots or energy[1] != 20*len(slots):
                raise AutomationError("怪物卡数量与电量不一致，未继续调整或出战。")
            if len(slots)==1:
                self.click(s,unique(s,"保存方案",(.75,.90,1,1)))
                return self.c.wait(self.challenge,self.c.settings.transition_timeout,"保存单张怪物卡")
            before=len(slots)
            self.click(s,slots[-1])
            s=self.snap()
            if len(enemy_slots(s))!=before-1:
                raise AutomationError("移除怪物卡后数量未减少，已停止，未出战。")
        raise AutomationError("最低电量配置超出有限调整次数。")

    def catalog(self):
        from .workflows import DailyWorkflow,TABS
        from .config import ROOT,RESOURCE_ROOT
        daily=DailyWorkflow(self.c)
        for _ in range(10):
            s=self.snap()
            if daily._handbook(s):break
            if self.earned_reward(s):self.click(s,self.earned_reward(s));continue
            if settlement(s) and self.complete_button(s):
                self.click(s,self.complete_button(s))
                self.c.wait(lambda f:self.challenge(f) or self.earned_reward(f),self.c.settings.transition_timeout,"结算退出",allow_blank=True)
                continue
            if self.presets(s) or self.squad(s) or self.challenge(s) or self.material_menu(s):
                self.c.key("esc",s);continue
            if daily._main(s):daily._open_daily();break
            self.c.evidence(s,"battle_catalog_unknown")
            self.c.wait(lambda f:daily._entry_ready(f) or daily._handbook(f) or settlement(f)
                        or self.earned_reward(f) or self.squad(f) or self.challenge(f) or self.material_menu(f),
                        self.c.settings.transition_timeout,"读取目录前的游戏转场",allow_blank=True)
        else:raise AutomationError("读取目录前返回手册次数超过上限。")
        s=self.snap();self.click(s,unique(s,"训练",TABS))
        self.c.wait(self.categories_ready,self.c.settings.transition_timeout,"四个训练分类")
        base=json.loads((RESOURCE_ROOT/"assets"/"training_catalog.json").read_text(encoding="utf-8"))
        collected={}
        for category in CATEGORIES:
            s=self.snap();self.categories_ready(s);self.click(s,unique(s,category,(.05,.25,.25,.90)))
            seen=set();titles=[]
            for _ in range(8):
                s=self.snap();self.c.evidence(s,"battle_catalog_"+category)
                buttons=[t for t in s.tokens if normalize(t.text)=="前往" and t.within((.60,.20,.89,.93))]
                for button in buttons:
                    y=button.center[1]
                    candidates=[t for t in s.tokens if t.within((.25,max(.22,y-.18),.67,y-.028))
                                and len(normalize(t.text))>=2 and not re.fullmatch(r"[0-9 /]+",t.text)
                                and not t.text.startswith(("推荐等级","挑战等级"))]
                    if candidates:
                        title=max(candidates,key=lambda t:(t.box[3]-t.box[1],t.box[2]-t.box[0])).text
                        if title not in titles:titles.append(title)
                sig=tuple((normalize(t.text),round(t.center[1],2)) for t in s.tokens if t.within((.25,.23,.90,.90)))
                if sig in seen:break
                seen.add(sig)
                self.c.move(s,(.67,.65));self.c.scroll(-360)
            collected[category]=titles
            for title in titles:base.setdefault(category,{}).setdefault(title,[])
        (ROOT/"training_catalog.json").write_text(json.dumps(base,ensure_ascii=False,indent=2),encoding="utf-8")
        self.details.update(catalog=collected,catalog_file=str(ROOT/"training_catalog.json"),submitted=False)
        self.c.report.stamina_spent=0
        return TaskResult("battle","ok","已读取四个训练分类的当前可见副本目录；未进入战斗。")

    def resume(self, path):
        from dataclasses import replace
        from .battle_engine import execute_battle
        previous=json.loads(Path(path).read_text(encoding="utf-8"))
        batch=previous.get("battle",{})
        old=next((b for b in reversed(batch.get("battles",[])) if b.get("energy_pending")),batch)
        if (previous.get("server")!=self.c.settings.server or not same_battle_plan(old.get("plan",{}),asdict(self.plan))
            or not old.get("submitted") or old.get("completed") or not old.get("energy_pending")):
            raise AutomationError("续接报告的区服、方案或未结算出战证据不符；不执行战斗。")
        strategy=self.details.get("strategy")
        self.details.update(old)
        self.details.update(resumed_from=str(path),completed=False,resumed_from_plan=old.get("plan"),plan=asdict(self.plan))
        if strategy:self.details["strategy"]=strategy
        self.c.report.stamina_spent=None
        s=self.snap()
        paused=s.has("退出战斗",(.75,.89,1,1)) and s.has("战况",(.68,0,.88,.10)) and s.has("关卡详情")
        if paused:
            self.c.key("esc",s)
            self.c.sleep(.3)
            s=self.snap()
        self.c.evidence(s,"battle_resume_hud")
        if settlement(s):
            from .battle_engine import finish_settlement
            finish_settlement(self.c,s)
        else:
            if not combat_hud(s):
                self.c.key("esc",s)
                raise AutomationError("续接时战斗HUD仍未核实；已按Esc暂停保护角色，未重新出战。")
            execute_battle(self.c,replace(self.plan,intro_wait=0))
        if batch.get("budget_file"):
            from .battle_budget import BattleBudget
            budget=BattleBudget(Path(batch["budget_file"]).parent,batch["budget_id"],self.c.settings.server,batch["energy_allowance"],batch["battle_limit"])
            budget.settle(self.details["reservation"],self.details.get("energy_after"))
        return TaskResult("battle","ok","已在同一场战斗取得结算；未再次出战。")

    def _run_one(self, prepare_only=False):
        self.plan.validate()
        self.details.update(plan=asdict(self.plan), prepared=False, submitted=False, completed=False)
        s=self.open_subcategory(self.training())
        s=self.select_stage(s)
        from .battle_vision import energy_readout, selected_stage
        if not selected_stage(s,self.plan.stage):
            raise AutomationError("没有核实副本卡片的选中高亮。")
        energy=energy_readout(s)
        if not energy:
            raise AutomationError("未核实当前电量与本次消耗，禁止下一步。")
        available,cost=energy
        self.details.update(energy_before=available,energy_cost=cost)
        if cost>self.plan.max_energy and self.plan.category == "实战模拟室":
            s=self.minimum_configuration(s)
            energy=energy_readout(s)
            if not energy:raise AutomationError("最低电量配置保存后未核实电量。")
            available,cost=energy
            self.details.update(energy_before=available,energy_cost=cost)
        if cost>self.plan.max_energy or available<cost:
            raise AutomationError(f"本次显示消耗 {cost} 电量，上限 {self.plan.max_energy}、现有 {available}；请在游戏内调整为最低配置后重试。")
        self.click(s,unique(s,"下一步",(.80,.90,1,1)))
        s=self.c.wait(self.squad,self.c.settings.transition_timeout,"副本配队",allow_blank=True)
        s=self.select_team(s)
        final_energy=energy_readout(s)
        if final_energy != (available,cost):
            raise AutomationError("应用队伍后电量或消耗发生变化，禁止出战。")
        self.details["prepared"]=True
        if prepare_only:
            return TaskResult("battle","ok","副本、电量和指定队伍已核实；仅准备，未出战。")
        target=unique(s,"出战",(.82,.90,1,1))
        if not target and self.squad(s):
            from .battle_vision import local_start_button
            target=local_start_button(s,self.c.vision)
            if target:
                s.tokens.append(target)
                self.c.log("全屏OCR未读到出战；按钮局部原分辨率识别已核实出战文字。")
        self.c.evidence(s,"battle_before_start")
        if not self.details.get("team_verified") or not target:
            raise AutomationError("队伍或出战按钮没有核实。")
        # Mark the intent once; never replay an ambiguous submitted battle after pause.
        reservation = self.budget.reserve(cost,self.c.report.run_dir) if self.budget else None
        self.details["reservation"] = reservation
        self.submitted=True
        self.details.update(submitted=True,energy_pending=True)
        self.c.report.stamina_spent=None
        self.click(s,target)
        from .battle_engine import execute_battle
        execute_battle(self.c,self.plan)
        return TaskResult("battle","ok","已识别战斗结算，所有按键已释放；本次副本结束，不再挑战。")


    def run(self, prepare_only=False):
        from copy import deepcopy
        import uuid
        from .config import ROOT
        from .battle_budget import BattleBudget, budget_directory
        if prepare_only:return self._run_one(prepare_only=True)
        self.plan.validate()
        identifier=self.plan.budget_id or uuid.uuid4().hex
        self.budget=BattleBudget(budget_directory(),identifier,self.c.settings.server,self.plan.energy_budget,self.plan.battles)
        batch=dict(budget_id=identifier,budget_file=str(self.budget.path),energy_allowance=self.plan.energy_budget,
                   battle_limit=self.plan.battles,battles=[],completed=False)
        if self.details.get("strategy"):batch["strategy"]=self.details["strategy"]
        confirmed=0
        try:
            if self.budget.pending:
                raise AutomationError("预算有未结算出战，请先续接该场战斗，不开启下一场。")
            for number in range(min(self.plan.battles,self.budget.rounds_left)):
                self.details={}
                self.c.report.battle=self.details
                self.submitted=False
                self.c.log(f"批次场次 {number+1}/{self.plan.battles}；剩余授权电量 {self.budget.remaining}。")
                try:
                    result=self._run_one()
                    self.budget.settle(self.details["reservation"],self.details.get("energy_after"))
                    confirmed+=self.details["energy_cost"]
                    batch["battles"].append(deepcopy(self.details))
                except Exception as error:
                    self.details["error"]=str(error)
                    batch["battles"].append(deepcopy(self.details))
                    raise
                self.c.log(f"第 {number+1} 场结算已核实；本批次已用 {confirmed} 电量。")
                if self.budget.remaining<20 or self.budget.rounds_left==0:break
            batch["completed"]=bool(batch["battles"]) and all(b.get("completed") for b in batch["battles"])
            return TaskResult("battle","ok",f"本批次 {len(batch['battles'])} 场结算已核实，使用 {confirmed} 电量；未补充电量。")
        finally:
            batch.update(energy_reserved=self.budget.reserved,energy_remaining=self.budget.remaining,pending=bool(self.budget.pending))
            self.c.report.stamina_spent=None if self.budget.pending else confirmed
            self.c.report.battle=batch
