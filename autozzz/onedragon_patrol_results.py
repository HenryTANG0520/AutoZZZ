"""巡逻报告的只读展示，沿用记录中的路线编号和名称。"""
from __future__ import annotations

from .localization import tr


OUTCOME_LABELS = {
    "completed": "巡逻已完成",
    "incomplete": "巡逻未完成",
    "no_work": "巡逻无需执行",
    "failed": "巡逻失败",
    "stopped": "巡逻已停止",
    "unverified": "巡逻结果未核实",
    "unrecorded": "巡逻结果未记录",
}
ROUTE_LABELS = {
    "completed": "通过",
    "failed": "失败",
    "skipped": "已跳过",
    "already_finished": "跳过（已有完成记录）",
}
ATTENTION = ("incomplete", "failed", "stopped", "unverified", "unrecorded")


def _record(value):
    return value if isinstance(value, dict) else {}


def _rows(value):
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def patrol_result(execution, app_id=None, *, route_debug=False):
    if route_debug:
        return None
    execution = _record(execution)
    result = _record(execution.get("world_patrol"))
    outcome = result.get("outcome")
    if isinstance(outcome, str):
        return result if outcome in OUTCOME_LABELS else dict(result, outcome="unverified")
    ran_patrol = app_id == "world_patrol" and execution.get("state") == "success"
    if ran_patrol or any(row.get("id") == "world_patrol" and row.get("state") == "success"
                         for row in _rows(execution.get("applications"))):
        return {"outcome": "unrecorded"}
    return None


def patrol_label(outcome):
    return tr(OUTCOME_LABELS.get(outcome, OUTCOME_LABELS["unverified"]))


def patrol_attention(outcomes):
    return next((outcome for outcome in ATTENTION if outcome in outcomes), None)


def patrol_counts(result):
    counts = _record(result.get("route_counts"))
    if not counts:
        return ""
    counts = {key: counts[key] if type(counts.get(key)) is int and counts[key] >= 0 else 0
              for key in ROUTE_LABELS}
    return tr("通过 {completed} · 失败 {failed} · 跳过 {skipped} · 已有完成记录 {already_finished}", **counts)


def report_patrols(report):
    if report.get("operation") == "account_rotation":
        accounts = {str(row.get("idx")): row.get("name") for row in _rows(report.get("accounts"))}
        results = []
        for stage in _rows(report.get("stages")):
            if stage.get("method") != "start_application":
                continue
            execution = _record(stage.get("execution"))
            patrol = patrol_result(execution, execution.get("prepared_app"))
            if patrol:
                idx = str(stage.get("instance_idx", ""))
                results.append((accounts.get(idx) or idx, patrol))
        return results
    execution = _record(report.get("execution"))
    if "state" not in execution:
        execution = dict(execution, state=report.get("status"))
    route_debug = any(_record(report.get(section)).get("route_debug") for section in ("connection", "request"))
    patrol = patrol_result(execution, report.get("app_id"), route_debug=route_debug)
    return [("", patrol)] if patrol else []


def report_patrol_outcome(sections):
    outcomes = [patrol["outcome"] for _, patrol in sections]
    return (patrol_attention(outcomes) or outcomes[0]) if outcomes else None


def _patrol_lines(result):
    lines = [patrol_label(result["outcome"])]
    counts = patrol_counts(result)
    if counts:
        lines.append(counts)
    before = _record(result.get("progress_before")).get("completed_rounds")
    after = _record(result.get("progress_after")).get("completed_rounds")
    if type(before) is int and type(after) is int:
        lines.append(tr("SDK 计入轮数 {before} → {after}", before=before, after=after))
    names = _record(result.get("route_names"))
    for row in _rows(result.get("routes")):
        route_id = row.get("route_id")
        if not isinstance(route_id, str) or not route_id:
            continue
        name = names.get(route_id)
        route = f"{name} [{route_id}]" if isinstance(name, str) and name else route_id
        label = tr(ROUTE_LABELS.get(str(row.get("outcome")), "结果未核实"))
        lines.append(tr("第 {number} 轮 · {outcome} · {route}",
                        number=row.get("round", "—"), outcome=label, route=route))
        if isinstance(row.get("status"), str) and row["status"]:
            lines.append("    " + row["status"])
    omitted = result.get("omitted_routes")
    if type(omitted) is int and omitted > 0:
        lines.append(tr("另有 {count} 条较早记录，见同目录运行日志。", count=omitted))
    if result["outcome"] == "unrecorded":
        lines.append(tr("旧报告的逐路线结果见同目录运行日志。"))
    return "\n".join(lines)


def format_patrol_report(sections, original):
    if not sections:
        return original
    blocks = [(str(account) + "\n" if account else "") + _patrol_lines(result) for account, result in sections]
    return "\n\n".join([*blocks, tr("原始报告 JSON") + "\n" + original])
