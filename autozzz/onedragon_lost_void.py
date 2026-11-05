"""Preserve Lost Void clear, retreat, and reward results across SDK cleanup."""
from contextlib import ExitStack, contextmanager
import copy
from functools import wraps


_RESULT_SCREEN = "迷失之地-挑战结果"
_REWARD_ICONS = ("奖励-零号业绩", "奖励-丁尼")
_CAP_FIELDS = ("eval_point_complete", "period_reward_complete")
_STABLE_REWARDS = "最终结算奖励栏已核实"
_INCOMPLETE_REWARDS = "最终结算奖励栏观察不完整"


def _reward_area(level, name):
    area = level.ctx.screen_loader.get_area(_RESULT_SCREEN, name)
    if area is None or not area.is_template_area:
        return None
    template = level.ctx.tm.template_loader.get_template(area.template_sub_dir, area.template_id)
    if template is None or template.raw is None or template.mask is None:
        return None
    height, width = template.raw.shape[:2]
    if (height > area.rect.height or width > area.rect.width or not template.raw.any()
            or template.mask.shape[:2] != (height, width) or not template.mask.any()):
        return None
    return area.rect


def _reward_slots(level, frame, rect):
    import cv2
    import numpy as np

    panel = frame[rect.y1:rect.y2, rect.x1:rect.x2]
    if panel.shape[:2] != (348, 522):
        return None
    # The final result shows two rows of four quantities or explicit EMPTY labels.
    labels = np.vstack((panel[136:183], panel[294:341]))
    labels = cv2.resize(labels, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    height, width = labels.shape[:2]
    slots = {}
    for item in level.ctx.ocr.ocr(labels, threshold=.8):
        value = item.data.strip()
        quantity = value.replace(",", "")
        if value != "EMPTY" and not (quantity.isdecimal() and int(quantity) > 0):
            continue
        col = item.center.x * 4 // width
        row = item.center.y * 2 // height
        slot = row, col
        if not (0 <= row < 2 and 0 <= col < 4) or slot in slots:
            return None
        slots[slot] = value
    if len(slots) == 8:
        return tuple(value for _, value in sorted(slots.items()))
    return None


def _observe_settlement(level):
    eval_area = _reward_area(level, _REWARD_ICONS[0])
    money_area = _reward_area(level, _REWARD_ICONS[1])
    if eval_area is None or money_area is None:
        return "结算奖励模板不可用"
    frame = level.last_screenshot
    slots = None
    if frame is not None and all(level.round_by_find_area(frame, _RESULT_SCREEN, name).is_success
                                 for name in ("标题-挑战结果", "按钮-完成")):
        if level.round_by_find_area(frame, _RESULT_SCREEN, _REWARD_ICONS[0]).is_success:
            level.reward_eval_found = True
            return "已识别零号业绩奖励"
        if level.round_by_find_area(frame, _RESULT_SCREEN, _REWARD_ICONS[1]).is_success:
            level.reward_dn_found = True
        slots = _reward_slots(level, frame, eval_area)

    count, previous_at, previous_slots = getattr(level, "_autozzz_reward_sample", (0, 0, None))
    observed_at = level.last_screenshot_time
    level._autozzz_reward_sample = count + 1, observed_at, slots
    if slots is not None and slots == previous_slots and 0 < observed_at - previous_at <= 2:
        return _STABLE_REWARDS
    if count >= 2:
        return _INCOMPLETE_REWARDS
    return None


def _patch_node(stack, ctx, cls, name, handler):
    original = getattr(cls, name)

    @wraps(original)
    def wrapped(operation):
        if operation.ctx is ctx:
            return handler(operation, original)
        return original(operation)

    node = copy.copy(original.operation_node_annotation)
    node.op_method = wrapped
    wrapped.operation_node_annotation = node
    stack.callback(setattr, cls, name, original)
    setattr(cls, name, wrapped)


def _defer_completion(operation, original, fields):
    # The SDK property setters persist immediately. Filter only the new
    # completion writes from this observation before they reach disk.
    record = operation.run_record
    update = record.update
    previous_override = vars(record).get("update")

    def checked_update(key, value, save=True):
        if key not in fields or value is not True:
            return update(key, value, save)

    record.update = checked_update
    try:
        return original(operation)
    finally:
        if previous_override is None:
            del record.update
        else:
            record.update = previous_override


@contextmanager
def preserve_failed_exit(ctx):
    """Install the Lost Void outcome adapters for the duration of a run."""
    from one_dragon.utils.log_utils import log
    from zzz_od.application.hollow_zero.lost_void.lost_void_app import LostVoidApp
    from zzz_od.application.hollow_zero.lost_void.operation.lost_void_run_level import LostVoidRunLevel

    def finish_exit(operation, original):
        result = original(operation)
        if result.is_success and result.status == LostVoidRunLevel.STATUS_COMPLETE:
            if operation.previous_node.name == "处理战斗失败":
                return operation.round_fail("迷失之地战斗失败，已撤退。")
            return operation.round_fail("迷失之地挑战重试已耗尽，已退出。")
        return result

    def settle(level, original):
        if level.node_clicked:
            proof = getattr(level, "_autozzz_reward_proof", _INCOMPLETE_REWARDS)
        else:
            proof = _observe_settlement(level)
            if proof is None:
                return level.round_wait("等待核实迷失之地结算奖励", wait=.4)
        if proof == _STABLE_REWARDS:
            result = original(level)
        else:
            result = _defer_completion(level, original, _CAP_FIELDS)
        if level.node_clicked:
            level._autozzz_reward_proof = proof
        if result.is_success:
            log.info("迷失之地奖励结算：%s", proof)
        return result

    def check_progress(app, original):
        return _defer_completion(app, original, ("bounty_commission_complete",))

    def claim(app, original):
        result = original(app)
        if (result.is_success and result.status == "按钮-悬赏委托-全部领取"
                and app.config.is_bounty_commission_mode and not app.run_record.bounty_commission_complete):
            # In bounty mode the SDK reaches this node only after recognizing 8000/8000.
            app.run_record.bounty_commission_complete = True
        return result

    def return_after_claim(app, original):
        claim_result = app.previous_node
        result = original(app)
        if result.is_success and not claim_result.is_success and not app.run_record.bounty_commission_complete:
            detail = claim_result.status or "领取结果未知"
            return app.round_fail(f"迷失之地悬赏奖励领取未核实（{detail}）。")
        return result

    with ExitStack() as stack:
        _patch_node(stack, ctx, LostVoidRunLevel, "handle_fail_exit", finish_exit)
        _patch_node(stack, ctx, LostVoidRunLevel, "handle_challenge_result_finish", settle)
        _patch_node(stack, ctx, LostVoidApp, "check_bounty_commission_before", check_progress)
        _patch_node(stack, ctx, LostVoidApp, "claim_all", claim)
        _patch_node(stack, ctx, LostVoidApp, "back_at_last", return_after_claim)
        yield
