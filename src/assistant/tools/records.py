"""read_experiment_records 工具：读 src/records/store 的实验记录（只读）。

favorite_id 给定时只看该单体组；缺省时返回最近若干条（全局面貌）。
输出含**完整实验流程（process_notes）**、**全部时间线条目**、实验起始时间
（时间线首点派生的 experiment_date）、自我总结（self_summary）、本人认为的
失误（mistakes），全部是实验级事实（人填的 ground truth），供助手引用。

v1.9.3（问题 4.1）：此前只输出最近 5 条时间点、每条截 120 字且**完全不含
process_notes**，导致助手答不出「完整实验流程」。现在列表输出给流程要点 +
全部时间点（按字符预算截断并提示），并新增 `read_experiment_record` 针对
单条记录给出全文（含全部时间线与附件清单）。
"""

from __future__ import annotations

_MAX_RECORDS = 10      # 单次最多返回条数（token 成本控制）
_MAX_FIELD = 200       # 长文本字段截断长度
_MAX_PROCESS = 1200    # 列表输出中「完整实验流程」注入上限（单条全量见下方工具）
_FULL_PROCESS = 6000   # 单条工具：流程全文上限
_MAX_TIMELINE_CHARS = 4000   # 列表输出：全部时间点的总字符预算
_MAX_TIMELINE_CHARS_FULL = 8000  # 单条工具：时间线总字符预算
_MAX_SEGMENT = 600     # 单条时间点描述截断长度

_OUTCOME_ZH = {"film": "成膜", "partial": "部分成膜", "failed": "失败"}


def _cut(s: str, n: int = _MAX_FIELD) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[:n] + "…"


def _fmt_timeline(timeline: list, budget: int,
                  max_segment: int = _MAX_SEGMENT) -> list[str]:
    """全部时间点（按顺序，含第一条）；超出字符预算时截断并如实注明。"""
    lines: list[str] = []
    used = 0
    shown = 0
    total = len(timeline)
    for entry in timeline:
        if not isinstance(entry, dict):
            continue
        label = str(entry.get("time_label") or "?").strip()
        desc = (str(entry.get("description") or "")).strip().replace("\n", " ")
        if len(desc) > max_segment:
            desc = desc[:max_segment] + "…"
        line = f"  · {label}：{desc}"
        atts = entry.get("attachments") or []
        if atts:
            names = [str(a.get("filename") or "") for a in atts
                     if isinstance(a, dict)]
            names = [n for n in names if n]
            if names:
                line += f"［附件：{'，'.join(names[:5])}］"
        if used + len(line) > budget and shown > 0:
            lines.append(f"  …（余 {total - shown} 条因长度省略，"
                         "需要全文请调 read_experiment_record）")
            break
        lines.append(line)
        used += len(line)
        shown += 1
    return lines


def _fmt_record(rec: dict) -> str:
    outcome = _OUTCOME_ZH.get(rec.get("outcome"), rec.get("outcome") or "未填")
    status_zh = "草稿" if rec.get("status") == "draft" else "正式"
    exp_date = rec.get("experiment_date") or rec.get("date") or "日期未知"
    date_bit = f"实验时间 {exp_date}"
    if rec.get("date_source") == "created":
        date_bit += f"（时间线无可解析时间点，回退录入日期；录入 {rec.get('date') or '?'}）"
    elif rec.get("date") and exp_date != rec.get("date"):
        date_bit += f"（录入 {rec['date']}）"
    lines = [
        f"### {rec.get('record_id')}（{date_bit}，{status_zh}，结果：{outcome}）",
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
    process = str(rec.get("process_notes") or "").strip()
    if process:
        truncated = len(process) > _MAX_PROCESS
        lines.append(f"- 实验流程（原文共 {len(process)} 字"
                     + ("，以下为要点摘录" if truncated else "，全文")
                     + "）：" + (process[:_MAX_PROCESS] + "…" if truncated else process))
    timeline = rec.get("timeline") or []
    if timeline:
        lines.append(f"- 实验过程时间线（共 {len(timeline)} 条，按顺序）：")
        lines.extend(_fmt_timeline(timeline, _MAX_TIMELINE_CHARS))
    else:
        lines.append("- 实验过程时间线：（未填写）")
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

    # 新的在前（list_records 按实验时间升序，反转载取最近 N 条）
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
            "timeline_counts": {str(r.get("record_id")):
                                len(r.get("timeline") or []) for r in recent},
        },
        "is_error": False,
    }


def read_experiment_record(record_id: str,
                           include_timeline: bool = True) -> dict:
    """单条实验记录全文（v1.9.3）：完整实验流程 + 全部时间点 + 附件清单。

    列表工具（read_experiment_records）会截断长流程/长时间线；需要「完整实验
    流程」细节时用本工具按 record_id 取全文。
    """
    rid = str(record_id or "").strip()
    if not rid:
        return {"text": "参数缺失：需要 record_id（如 rec_20260902_006）",
                "details": {}, "is_error": True}
    try:
        try:
            from src.records import store as rec_store
        except ImportError:  # pragma: no cover
            from records import store as rec_store  # type: ignore
        rec = rec_store.get_record(rid)
    except Exception as exc:
        return {"text": f"实验记录读取失败：{type(exc).__name__}: {exc}",
                "details": {}, "is_error": True}
    if not rec:
        return {"text": f"未查到实验记录 {rid}（记录不存在或编号有误）。",
                "details": {"record_id": rid}, "is_error": False}
    try:
        try:
            from src.records import store as rec_store2
        except ImportError:  # pragma: no cover
            from records import store as rec_store2  # type: ignore
        rec = rec_store2._normalize_record(rec)
    except Exception:
        pass

    outcome = _OUTCOME_ZH.get(rec.get("outcome"), rec.get("outcome") or "未填")
    exp_date = rec.get("experiment_date") or rec.get("date") or "日期未知"
    ald = rec.get("aldehyde") or {}
    amine = rec.get("amine") or {}
    lines = [
        f"## {rid}（实验时间 {exp_date}，结果：{outcome}）",
        f"- 录入日期：{rec.get('date') or '?'}"
        + (f"；时间线首个时间点：{rec.get('date_label')}"
           if rec.get("date_source") == "timeline" and rec.get("date_label")
           else "；时间线无可解析时间点（显示录入日期）"),
        f"- 实验编号：{rec.get('experiment_no') or '（未填）'}；"
        f"操作人：{rec.get('operator') or '（未填）'}；状态："
        + ("草稿" if rec.get("status") == "draft" else "正式"),
        f"- 单体：醛 {ald.get('name') or ald.get('cas') or '?'}"
        f" / 胺 {amine.get('name') or amine.get('cas') or '?'}",
    ]
    cond = rec.get("conditions") or {}
    cond_bits = [f"{k}={v}" for k, v in cond.items()
                 if isinstance(v, str) and v.strip()]
    if cond_bits:
        lines.append("- 条件：" + "；".join(cond_bits))
    if rec.get("notes"):
        lines.append(f"- 备注：{rec['notes']}")
    if rec.get("self_summary"):
        lines.append(f"- 自我总结：{rec['self_summary']}")
    if rec.get("mistakes"):
        lines.append(f"- 本人认为的失误：{rec['mistakes']}")
    process = str(rec.get("process_notes") or "").strip()
    if process:
        truncated = len(process) > _FULL_PROCESS
        lines.append(f"\n### 完整实验流程（原文 {len(process)} 字"
                     + ("，已截断" if truncated else "") + "）\n"
                     + (process[:_FULL_PROCESS] + "…" if truncated else process))
    timeline = rec.get("timeline") or []
    if include_timeline and timeline:
        lines.append(f"\n### 实验过程时间线（共 {len(timeline)} 条，完整）")
        lines.extend(_fmt_timeline(timeline, _MAX_TIMELINE_CHARS_FULL))
    attachments = rec.get("attachments") or []
    if attachments:
        names = [str(a.get("filename") or "") for a in attachments
                 if isinstance(a, dict)]
        lines.append("\n### 记录级附件\n- " + "；".join(
            n for n in names if n))
    return {
        "text": "\n".join(lines),
        "details": {"record_id": rid,
                    "timeline_count": len(timeline),
                    "process_chars": len(process),
                    "outcome": rec.get("outcome"),
                    "experiment_date": rec.get("experiment_date"),
                    "date_source": rec.get("date_source")},
        "is_error": False,
    }


# ---------------------------------------------------------------- 草稿（写）

def _rec_store():
    try:
        from src.records import store as rec_store
    except ImportError:  # pragma: no cover
        from records import store as rec_store  # type: ignore
    return rec_store


def _find_dup_draft(rec_store, favorite_id: str | None,
                    canon_ald: str | None, canon_amine: str | None,
                    notes: str) -> dict | None:
    """幂等去重：同收藏（或同单体对的游离草稿）且 notes 相同的草稿直接复用。"""
    try:
        if favorite_id:
            candidates = rec_store.list_records(favorite_id=favorite_id)
        else:
            candidates = [r for r in rec_store.list_records()
                          if not r.get("favorite_id")]
    except Exception:
        return None
    for r in candidates:
        if r.get("status") != "draft":
            continue
        if (r.get("notes") or "").strip() != notes:
            continue
        if favorite_id:
            return r
        ra = str((r.get("aldehyde") or {}).get("smiles") or "")
        rm = str((r.get("amine") or {}).get("smiles") or "")
        if canon_ald and canon_amine:
            try:
                from src.favorites import store as fav_store
            except ImportError:  # pragma: no cover
                from favorites import store as fav_store  # type: ignore
            if fav_store._canonical(ra) == canon_ald \
                    and fav_store._canonical(rm) == canon_amine:
                return r
    return None


def draft_experiment_record(args: dict) -> dict:
    """起草实验记录（草稿态）。favorite_id 或醛/胺 SMILES 至少给其一。

    草稿校验宽松（experiment_no / outcome 可留空），用户后续在实验记录页
    编辑转正。幂等：同收藏（或同单体对游离记录）且 notes 相同的草稿
    不重复创建。
    """
    args = args if isinstance(args, dict) else {}
    favorite_id = (args.get("favorite_id") or "").strip() or None
    ald = (args.get("aldehyde_smiles") or "").strip()
    amine = (args.get("amine_smiles") or "").strip()
    if not favorite_id and (not ald or not amine):
        return {"text": "参数缺失：需要 favorite_id，或同时提供 "
                        "aldehyde_smiles 与 amine_smiles（游离记录）",
                "details": {}, "is_error": True}
    outcome = (args.get("outcome") or "").strip()
    if outcome and outcome not in ("film", "partial", "failed"):
        return {"text": f"outcome 必须是 film / partial / failed 之一或留空，"
                        f"收到: {outcome}",
                "details": {}, "is_error": True}
    notes = (args.get("notes") or "").strip()

    rec_store = _rec_store()
    try:
        canon_ald = canon_amine = None
        if not favorite_id:
            try:
                from src.favorites import store as fav_store
            except ImportError:  # pragma: no cover
                from favorites import store as fav_store  # type: ignore
            canon_ald = fav_store._canonical(ald)
            canon_amine = fav_store._canonical(amine)
        dup = _find_dup_draft(rec_store, favorite_id, canon_ald, canon_amine,
                              notes)
        if dup is not None:
            return {"text": f"已存在相同内容的草稿（{dup.get('record_id')}），"
                            "未重复创建。",
                    "details": {"record_id": dup.get("record_id"),
                                "deduplicated": True},
                    "is_error": False}

        rec = rec_store.create_record(
            favorite_id=favorite_id,
            aldehyde_smiles=ald, amine_smiles=amine,
            conditions=args.get("conditions")
            if isinstance(args.get("conditions"), dict) else None,
            outcome=outcome,
            notes=notes,
            operator=(args.get("operator") or "").strip(),
            experiment_no=(args.get("experiment_no") or "").strip(),
            status="draft",
            self_summary=(args.get("self_summary") or "").strip(),
            mistakes=(args.get("mistakes") or "").strip(),
        )
    except (ValueError, KeyError) as exc:
        return {"text": str(exc), "details": {}, "is_error": True}
    except Exception as exc:
        return {"text": f"草稿保存失败：{type(exc).__name__}: {exc}",
                "details": {}, "is_error": True}

    ald_label = (rec.get("aldehyde") or {}).get("name") or \
        (rec.get("aldehyde") or {}).get("smiles") or "?"
    amine_label = (rec.get("amine") or {}).get("name") or \
        (rec.get("amine") or {}).get("smiles") or "?"
    return {
        "text": f"已起草实验记录草稿 {rec['record_id']}（醛 {ald_label} / 胺 "
                f"{amine_label}，状态：草稿）。可到「实验记录」页补充细节并转正；"
                "草稿不会进入正式统计。",
        "details": {"record_id": rec["record_id"], "status": "draft",
                    "favorite_id": rec.get("favorite_id")},
        "is_error": False,
    }


def draft_experiment_record_impact(args: dict) -> str:
    args = args if isinstance(args, dict) else {}
    target = args.get("favorite_id") or "游离记录（指定 SMILES）"
    return (f"将起草一份实验记录（关联：{target}），以草稿状态保存；"
            "不影响正式记录与统计，可稍后在实验记录页编辑或删除。")
