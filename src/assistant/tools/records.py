"""read_experiment_records 工具：读 src/records/store 的实验记录（只读）。

favorite_id 给定时只看该单体组；缺省时返回最近若干条（全局面貌）。
输出含时间线条目、自我总结（self_summary）、本人认为的失误（mistakes），
全部是实验级事实（人填的 ground truth），供助手引用。
"""

from __future__ import annotations

_MAX_RECORDS = 10      # 单次最多返回条数（token 成本控制）
_MAX_FIELD = 200       # 长文本字段截断长度
_MAX_TIMELINE = 5      # 每条记录最多展示的时间线条目数

_OUTCOME_ZH = {"film": "成膜", "partial": "部分成膜", "failed": "失败"}


def _cut(s: str, n: int = _MAX_FIELD) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[:n] + "…"


def _fmt_record(rec: dict) -> str:
    outcome = _OUTCOME_ZH.get(rec.get("outcome"), rec.get("outcome") or "未填")
    status_zh = "草稿" if rec.get("status") == "draft" else "正式"
    lines = [
        f"### {rec.get('record_id')}（{rec.get('date') or '日期未知'}，"
        f"{status_zh}，结果：{outcome}）",
        f"- 实验编号：{rec.get('experiment_no') or '（未填）'}；"
        f"操作人：{rec.get('operator') or '（未填）'}",
    ]
    ald = rec.get("aldehyde") or {}
    amine = rec.get("amine") or {}
    lines.append(
        f"- 单体：醛 {ald.get('name') or ald.get('cas') or '?'}"
        f" / 胺 {amine.get('name') or amine.get('cas') or '?'}")
    cond = rec.get("conditions") or {}
    cond_bits = [f"{k}={v}" for k, v in cond.items()
                 if isinstance(v, str) and v.strip()]
    if cond_bits:
        lines.append("- 条件：" + "；".join(cond_bits[:8]))
    if rec.get("notes"):
        lines.append(f"- 备注：{_cut(rec['notes'])}")
    if rec.get("self_summary"):
        lines.append(f"- 自我总结：{_cut(rec['self_summary'])}")
    if rec.get("mistakes"):
        lines.append(f"- 本人认为的失误：{_cut(rec['mistakes'])}")
    timeline = rec.get("timeline") or []
    if timeline:
        lines.append(f"- 时间线（共 {len(timeline)} 条，示最近 "
                     f"{min(len(timeline), _MAX_TIMELINE)} 条）：")
        for entry in timeline[-_MAX_TIMELINE:]:
            if isinstance(entry, dict):
                lines.append(f"  · {entry.get('time_label') or '?'}："
                             f"{_cut(entry.get('description') or '', 120)}")
    return "\n".join(lines)


def read_experiment_records(favorite_id: str | None = None) -> dict:
    """读实验记录。favorite_id 可选；无记录时如实说"系统内未查到"。"""
    try:
        try:
            from src.records import store as rec_store
        except ImportError:  # pragma: no cover
            from records import store as rec_store  # type: ignore

        fid = (favorite_id or "").strip() or None
        recs = rec_store.list_records(favorite_id=fid)
    except Exception as exc:
        return {"text": f"实验记录读取失败：{type(exc).__name__}: {exc}",
                "details": {}, "is_error": True}

    if not recs:
        scope = f"收藏 {fid} 名下" if fid else "系统内"
        return {"text": f"{scope}未查到实验记录。",
                "details": {"count": 0, "favorite_id": fid}, "is_error": False}

    # 新的在前（list_records 按日期升序，反转载取最近 N 条）
    recent = list(reversed(recs))[:_MAX_RECORDS]
    header = (f"共 {len(recs)} 条实验记录"
              + (f"（收藏 {fid}）" if fid else "")
              + f"，以下为最近 {len(recent)} 条：")
    text = header + "\n\n" + "\n\n".join(_fmt_record(r) for r in recent)
    return {
        "text": text,
        "details": {
            "count": len(recs),
            "favorite_id": fid,
            "record_ids": [r.get("record_id") for r in recent],
        },
        "is_error": False,
    }
