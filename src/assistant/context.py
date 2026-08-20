"""页⑤转入上下文组装：单体组信息 + 最新迭代建议 + 实验记录摘要。

context 结构（前端契约）：{favorite_id?, ald_smiles?, amine_smiles?,
suggestion_ids?}。全部数据源只读；任何一路失败降级跳过，绝不让建档失败。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_MAX_SUGGESTIONS = 3   # 注入的最新迭代建议条数
_MAX_RECORDS = 5       # 注入的最近实验记录条数


def _monomer_block(context: dict) -> str:
    """单体组信息：优先 favorite 冗余的单体对象；否则按 SMILES 反查内置库。"""
    ald = amine = None
    fid = str(context.get("favorite_id") or "").strip()
    if fid:
        try:
            try:
                from src.favorites import store as fav_store
            except ImportError:  # pragma: no cover
                from favorites import store as fav_store  # type: ignore
            fav = fav_store.get_favorite(fid)
            if fav:
                ald, amine = fav.get("aldehyde"), fav.get("amine")
                snap = fav.get("latest_prediction") or {}
                snap_bit = ""
                if isinstance(snap, dict) and snap.get("score") is not None:
                    snap_bit = f"；最近打分 {snap.get('score')}"
                header = f"收藏条目 {fid}{snap_bit}"
            else:
                header = f"收藏条目 {fid}（未找到，可能已删除）"
        except Exception as exc:
            logger.info("context 注入：收藏读取降级 %s", exc)
            header = f"收藏条目 {fid}（读取失败）"
    else:
        header = ""

    if ald is None and (context.get("ald_smiles") or context.get("amine_smiles")):
        ald = {"smiles": context.get("ald_smiles") or ""}
        amine = {"smiles": context.get("amine_smiles") or ""}
        try:
            from api.deps import load_builtin_monomers
            by_smiles = load_builtin_monomers().get("by_smiles") or {}
            for obj in (ald, amine):
                hit = by_smiles.get(obj.get("smiles") or "")
                if hit:
                    obj.update({"name": hit.get("name", ""),
                                "cas": hit.get("cas", "")})
        except Exception as exc:
            logger.info("context 注入：内置库反查降级 %s", exc)

    if ald is None and amine is None:
        return header

    def _fmt(m: dict | None, role: str) -> str:
        m = m or {}
        bits = [b for b in (m.get("name"), m.get("cas"), m.get("smiles")) if b]
        return f"{role}：" + " / ".join(str(b) for b in bits) if bits else ""

    lines = [l for l in (_fmt(ald, "醛单体"), _fmt(amine, "胺单体")) if l]
    return (header + "\n" if header else "") + "\n".join(lines)


def _suggestions_block(context: dict) -> str:
    """最新迭代建议：suggestion_ids 指定优先；否则按 favorite 取最新若干条。"""
    try:
        try:
            from src.rag import suggestions as sug_store
        except ImportError:  # pragma: no cover
            from rag import suggestions as sug_store  # type: ignore

        sugs: list[dict] = []
        for sid in context.get("suggestion_ids") or []:
            sug = sug_store.get_suggestion(str(sid))
            if sug:
                sugs.append(sug)
        if not sugs:
            fid = str(context.get("favorite_id") or "").strip() or None
            sugs = sug_store.list_suggestions(favorite_id=fid)[:_MAX_SUGGESTIONS]
    except Exception as exc:
        logger.info("context 注入：建议读取降级 %s", exc)
        return ""
    if not sugs:
        return ""

    lines = []
    for s in sugs:
        payload = s.get("payload") or {}
        bits = [f"- [{s.get('suggestion_id')}] 类型 {s.get('type') or '?'}，"
                f"状态 {s.get('status') or '?'}，"
                f"创建于 {s.get('created_at') or '?'}"]
        summary = payload.get("summary") or payload.get("rationale") or ""
        if summary:
            bits.append(f"  摘要：{str(summary)[:200]}")
        adj = payload.get("adjustments") or []
        if adj:
            bits.append(f"  调整 {len(adj)} 项：" +
                        "；".join(str(a)[:60] for a in adj[:3]))
        cand = payload.get("candidate") or payload.get("new_pair") or {}
        if isinstance(cand, dict) and cand:
            bits.append(f"  候选：{str(cand)[:160]}")
        lines.append("\n".join(bits))
    return "## 最新迭代建议\n" + "\n".join(lines)


def _records_block(context: dict) -> str:
    """实验记录摘要：条数 + 结果分布 + 最近若干条的自我总结/失误。"""
    fid = str(context.get("favorite_id") or "").strip() or None
    if fid is None:
        return ""  # 无锚点时不把全局记录塞进上下文（噪声）
    try:
        try:
            from src.records import store as rec_store
        except ImportError:  # pragma: no cover
            from records import store as rec_store  # type: ignore
        recs = rec_store.list_records(favorite_id=fid)
    except Exception as exc:
        logger.info("context 注入：记录读取降级 %s", exc)
        return ""
    if not recs:
        return "## 实验记录\n该单体组暂无实验记录。"

    dist: dict[str, int] = {}
    for r in recs:
        dist[r.get("outcome") or "未填"] = dist.get(r.get("outcome") or "未填", 0) + 1
    lines = [f"共 {len(recs)} 条，结果分布：" +
             "，".join(f"{k}×{v}" for k, v in sorted(dist.items()))]
    for r in list(reversed(recs))[:_MAX_RECORDS]:
        bit = f"- {r.get('record_id')}（{r.get('date') or '?'}，{r.get('outcome') or '未填'}）"
        if r.get("self_summary"):
            bit += f" 自我总结：{str(r['self_summary'])[:120]}"
        if r.get("mistakes"):
            bit += f" 失误：{str(r['mistakes'])[:120]}"
        lines.append(bit)
    return "## 实验记录摘要\n" + "\n".join(lines)


def build_context_block(context: dict | None) -> str:
    """组装注入 system prompt 的上下文块；空 context / 全部降级返回空串。"""
    if not isinstance(context, dict) or not context:
        return ""
    parts = []
    monomer = _monomer_block(context)
    if monomer:
        parts.append("## 当前单体组\n" + monomer)
    for block in (_suggestions_block(context), _records_block(context)):
        if block:
            parts.append(block)
    return "\n\n".join(parts)
