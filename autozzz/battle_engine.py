"""One battle, bounded script execution with a concurrent result watcher."""
from __future__ import annotations
import threading
import time
from .battle import combat_hud, parse_script, settlement, victory, guard_combat_scene
from .models import AutomationError, FocusLost, InputInterrupted, ScreenshotNotReady, Stopped
from .policy import guard_scene

class BattleEnded(Exception):
    pass


class BattleWatcher:
    def __init__(self, context, intro_wait):
        self.c=context
        self.intro_wait=intro_wait
        self.finished=threading.Event()
        self.ready=threading.Event()
        self.stop=threading.Event()
        self.scene=None
        self.state="loading"
        self.error=None
        self.updated=0.0
        self.epoch=-1
        self.last_combat_at=0
        self.thread=threading.Thread(target=self.watch,name="AutoZZZ-combat-watch",daemon=True)

    def watch(self):
        first_hud=None
        stable=0
        try:
            while not self.stop.is_set() and not self.finished.is_set():
                runner=self.c.runner
                if runner._stop.is_set(): return
                if runner._pause.is_set():
                    self.state="paused";self.ready.clear();first_hud=None;stable=0
                    self.stop.wait(.05);continue
                try:
                    epoch=runner.input_epoch
                    image,geometry=self.c.window.capture()
                    scene=self.c.vision.read(image,geometry)
                    self.scene=scene
                    self.updated=time.monotonic()
                    if epoch!=runner.input_epoch or runner._pause.is_set():
                        self.state="paused";self.ready.clear();first_hud=None;stable=0;continue
                    guard_combat_scene(scene)
                    if settlement(scene):
                        # Publish the stop flag before disk I/O or any additional OCR.
                        self.state="result";self.finished.set();self.ready.clear()
                        self.c.evidence(scene,"battle_settlement")
                        return
                    if combat_hud(scene):
                        if self.epoch!=epoch:
                            first_hud=None;stable=0
                        self.epoch=epoch
                        self.last_combat_at=time.monotonic()
                        stable+=1
                        first_hud=first_hud or time.monotonic()
                        if stable>=2 and time.monotonic()-first_hud>=self.intro_wait:
                            self.state="combat";self.ready.set()
                        else:self.state="intro"
                    else:
                        if self.ready.is_set() and self.epoch==epoch and time.monotonic()-self.last_combat_at<2:
                            self.state="combat"
                        else:
                            self.state="loading";self.ready.clear();first_hud=None;stable=0
                        # Known menus can never receive combat inputs.
                        if scene.has(("预备出战","预备编队","下一步","退出战斗","重新开始","挑战失败")):
                            raise AutomationError("战斗期间出现配置、暂停或失败页面，已停止战斗输入。")
                except (FocusLost,ScreenshotNotReady):
                    self.state="loading";self.ready.clear();first_hud=None;stable=0
                self.stop.wait(.12)
        except Exception as error:
            self.error=error;self.state="error";self.ready.clear()

    def input_guard(self):
        if self.finished.is_set():raise BattleEnded()
        if self.error:raise self.error
        self.c.input_checkpoint()
        if self.state!="combat" or not self.ready.is_set() or time.monotonic()-self.updated>3:
            raise InputInterrupted("战斗HUD暂不可用，释放按键，等待重新核实。")


def execute_battle(context, plan):
    watcher=BattleWatcher(context,plan.intro_wait)
    watcher.thread.start()
    details=context.report.battle
    details["actions"]=[]
    started=time.monotonic()
    inactive_before=context.runner.inactive_duration
    deadline=lambda: time.monotonic()-started-(context.runner.inactive_duration-inactive_before)<plan.timeout
    context.log(f"等待加载和战斗HUD；HUD出现后再等待 {plan.intro_wait:g} 秒转场。")
    entered_script=False
    try:
        def wait_ready():
            waiting=time.monotonic()
            inactive=context.runner.inactive_duration
            limit=10 if entered_script else context.settings.transition_timeout
            while deadline():
                context.checkpoint()
                if time.monotonic()-waiting-(context.runner.inactive_duration-inactive)>limit:
                    raise AutomationError("战斗HUD未及时核实，停止输入并暂停保护角色。")
                if watcher.finished.is_set():raise BattleEnded()
                if watcher.error:raise watcher.error
                if watcher.ready.wait(.05) and watcher.epoch==context.runner.input_epoch:
                    return
            raise AutomationError("等待战斗或结算超时；没有开始另一场挑战。")
        wait_ready()
        entered_script=True
        if watcher.scene:context.evidence(watcher.scene,"battle_ready")
        sequence=parse_script(plan.script,plan.hold_unit)
        for cycle in range(plan.cycles):
            for index,action in enumerate(sequence):
                wait_ready()
                context.scene_epoch=context.runner.input_epoch
                watcher.input_guard()
                entry=dict(cycle=cycle+1,index=index+1,key=action.key,seconds=action.seconds,status="attempted")
                details["actions"].append(entry)
                context.log(f"战斗脚本 {cycle+1}/{plan.cycles}，动作 {index+1}：{action.key.upper()} {action.seconds:g}秒")
                try:
                    context.window.combat_press(action.key,action.seconds,watcher.input_guard)
                    entry["status"]="released"
                except BattleEnded:
                    entry["status"]="settlement_released"
                    raise
                except InputInterrupted:
                    entry["status"]="interrupted_released"
                    context.log("战斗按键已释放；恢复后继续后续动作，不重放已发送的长按。","warning")
                    context.runner.wait_for_focus(context.window)
                # A short neutral gap is also result-monitored; no action_delay in combos.
                for _ in range(4):
                    if watcher.finished.wait(.05):raise BattleEnded()
                    context.checkpoint()
        context.log("预输入脚本已完成，保持所有键释放，只读等待结算。")
        result_wait=time.monotonic()
        while deadline() and time.monotonic()-result_wait<20:
            context.checkpoint()
            if watcher.finished.wait(.05):raise BattleEnded()
            if watcher.error:raise watcher.error
        raise AutomationError("脚本执行后未在时限内核实结算；已停止输入，不会再次出战。")
    except BattleEnded:
        watcher.stop.set();watcher.thread.join(timeout=5)
        finish_settlement(context,watcher.scene)
        context.log("已识别结算，立即结束脚本并释放按键；本轮不再发送任何战斗输入。")
    finally:
        watcher.stop.set()
        watcher.thread.join(timeout=5)
        if not watcher.finished.is_set() and watcher.scene:
            context.evidence(watcher.scene,"battle_interrupted")
            if not context.runner._stop.is_set():
                try:
                    current=context.snapshot()
                    if not settlement(current) and not current.has(("退出战斗","预备编队","预备出战","下一步")):
                        context.key("esc",current)
                        context.log("本场未核实结算，已按Esc暂停保护角色；不会重新出战。","warning")
                except (AutomationError,Stopped):pass


def finish_settlement(context,initial=None):
    """Stop on the first result frame, then only read while its text animates in."""
    from .battle_vision import result_energy
    details=context.report.battle
    def proof(scene):
        after=result_energy(scene)
        return victory(scene) and after is not None and 0<=details["energy_before"]-after<=details["energy_cost"]
    final=initial if initial is not None and proof(initial) else context.wait(proof,20,"完整战斗结算与电量",allow_blank=True)
    context.evidence(final,"battle_result_verified")
    details.update(completed=True,energy_pending=False,energy_after=result_energy(final))
    context.report.stamina_spent=details["energy_cost"]
    details["energy_accounting"]="出战前消耗与结算后剩余电量均已核实"
    return final
