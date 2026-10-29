"""按实际起始画面选择上游重新登录或游戏内换号。"""
from one_dragon.base.operation.operation_node import operation_node
from one_dragon.base.operation.operation_edge import node_from
from zzz_od.operation.zzz_operation import ZOperation
from zzz_od.operation.enter_game.enter_game import EnterGame
from zzz_od.operation.enter_game.switch_account import SwitchAccount


class LoginAccount(ZOperation):
    def __init__(self,ctx):
        super().__init__(ctx,op_name='登录指定账号')
        self.child=None;self.child_result=None

    @operation_node(name='识别账号起点',is_start_node=True)
    def recognize(self):
        probe=EnterGame(self.ctx,switch=True)
        if probe.round_by_ocr(self.last_screenshot,'今日到账').is_success and probe.round_by_ocr(self.last_screenshot,'月卡剩余').is_success:
            result=probe.round_by_ocr_and_click_by_priority(['今日到账'],screen=self.last_screenshot,pre_delay=0,success_wait=.5,retry_wait=.5)
            return self.round_wait('等待月卡领取后重新识别账号起点',wait=.5) if result.is_success else result
        world=probe.is_in_big_world(self.last_screenshot)
        if world is not None and world.is_success:
            self.child=SwitchAccount(self.ctx);return self.round_success()
        current,can_go=probe.check_screen_with_can_go(self.last_screenshot,'菜单')
        if can_go and current not in ('打开游戏','加载中'):
            self.child=SwitchAccount(self.ctx);return self.round_success()
        areas=('点击进入游戏','国服-账号密码','国服-账号密码-新','国服-账号密码进入游戏-新',
               '国际服-点击登录','国际服-密码输入区域','B服新-登录记录','B服新-手机号登录','标题-退出登录')
        for name in areas:
            if probe.round_by_find_area(self.last_screenshot,'打开游戏',name).is_success:
                self.child=probe;return self.round_success()
        return self.round_wait('等待登录页或可识别的大世界',wait=.5)

    @node_from(from_name='识别账号起点')
    @operation_node(name='执行账号登录')
    def login(self):
        if self.child_result is None:self.child_result=self.child.execute()
        return self.round_by_op_result(self.child_result)
