"""Non-combat actions only; state-aware checks before every input."""
from .models import AutomationError, Scene, Token, normalize

FORBIDDEN_ACTIONS = (
    "调频", "寻访", "抽取", "十连", "兑换", "充值", "购买菲林", "购买电量",
    "补充电量", "恢复电量", "以太电池", "开始战斗", "开始挑战", "出战", "作战开始",
    "角色升级", "音擎升级", "驱动盘强化", "改装", "合成", "继续挑战",
    "购买等级", "解锁丽都城募", "解锁成长计划", "购买成长计划", "选择奖励",
)
BLOCKING_DIALOGS = ("是否消耗菲林", "消耗菲林", "电量不足", "使用以太电池", "购买电量", "补充电量", "确认支付")


def guard_scene(scene: Scene) -> None:
    if scene.has(BLOCKING_DIALOGS):
        raise AutomationError("检测到付费、电量补充或战斗弹窗；非战斗每日已停止。")


def guard_click(scene: Scene, target: Token | tuple[float, float]) -> None:
    guard_scene(scene)
    if isinstance(target, Token):
        candidates = [target]
    else:
        x, y = target
        candidates = [token for token in scene.tokens if token.box[0] <= x <= token.box[2] and token.box[1] <= y <= token.box[3]]
    for token in candidates:
        value = normalize(token.text)
        if any(normalize(label) in value for label in FORBIDDEN_ACTIONS):
            raise AutomationError(f"非战斗每日禁止操作：{token.text}")


def coffee_is_free(scene: Scene) -> bool:
    """Only observed zero-cost coffee is allowed in this first implementation."""
    if scene.has(("菲林", "付费", "充值", "不免费", "非免费", "not free")):
        return False
    # The calibrated Tin Master dialogue directly redeems the daily free drink.
    options = [t for t in scene.tokens if t.within((1400/1920, 530/1080, 1915/1920, 835/1080))
               and normalize(t.text) == normalize("来杯汀曼特调")]
    dialogue = (scene.has("汀曼", (800/1920, 750/1080, 1100/1920, 850/1080))
                and scene.has(("想要喝点什么", "想喝点什么"), (500/1920, 800/1080, 1500/1920, 1040/1080))
                and len(options) == 1)
    return dialogue or any(normalize(t.text) in ("免费", "今日免费", "free", "0", "0丁尼") for t in scene.tokens if t.within((0.60, 0.60, 1, 1)))
