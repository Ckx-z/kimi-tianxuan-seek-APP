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
