"""文献录入路由：Crossref 查询生成待审核草稿 + 审核确认入库。

只进文献库（overlay：用户库 user_data_root/literature/paper_titles.json 优先，
源码态为 data/paper_titles.json）与审计流水（user_data_root/literature/
literature_intake.jsonl）；不入训练集、不入 GraphRAG 图（confirm 响应注明
graphrag_indexed:false）。
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException

from ..schemas import LiteratureConfirm, LiteratureLookup

router = APIRouter(prefix="/api/literature", tags=["literature"])


def _resolver():
    from literature import resolver
    return resolver


def _crossref():
    from literature import crossref
    return crossref


def _mark_existing(draft: dict) -> dict:
    """补 existing 标记：DOI 已在文献库中时为 True 并带 existing_paper_id。"""
    r = _resolver()
    hit = r.find_by_doi(draft.get("doi") or "")
    draft["existing"] = hit is not None
    if hit:
        draft["existing_paper_id"] = hit[0]
    return draft


@router.post("/lookup")
def lookup(req: LiteratureLookup):
    """doi 直接取元数据草稿；title 返回前 3 候选草稿。统一「待审核草稿」结构。"""
    doi = (req.doi or "").strip()
    title = (req.title or "").strip()
    if bool(doi) == bool(title):
        raise HTTPException(400, "doi 与 title 必须且只能提供一个")
    cx = _crossref()
    try:
        if doi:
            return {"draft": _mark_existing(cx.lookup_doi(doi))}
        candidates = [_mark_existing(d) for d in cx.search_by_title(title, rows=3)]
        return {"candidates": candidates}
    except cx.CrossrefNotFound as exc:
        raise HTTPException(404, str(exc))
    except cx.CrossrefError as exc:
        raise HTTPException(502, str(exc))


@router.post("/confirm", status_code=201)
def confirm(req: LiteratureConfirm):
    """审核后的草稿入库：追加 paper_titles.json + 审计流水；重复 DOI 409。"""
    r = _resolver()
    title = req.title.strip()
    if not title:
        raise HTTPException(400, "文献标题不能为空")
    doi = r.normalize_doi(req.doi)
    if doi:
        hit = r.find_by_doi(doi)
        if hit:
            raise HTTPException(409, detail={
                "message": f"该 DOI 已存在于文献库（paper_id={hit[0]}），未重复入库",
                "existing_paper_id": hit[0],
            })
    entry = {
        "doi": doi,
        "title": title,
        "authors": [str(a).strip() for a in req.authors if str(a).strip()],
        "journal": req.journal.strip(),
        "year": req.year,
        "abstract": (req.abstract or "").strip() or None,
        "in_training": False,
        "source": "user-intake",
        "added_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    url = f"https://doi.org/{doi}" if doi else None
    try:
        pid = r.append_paper(entry)
    except OSError as exc:
        raise HTTPException(500, f"文献库写入失败：{type(exc).__name__}: {exc}")
    try:
        r.append_intake({
            "action": "confirm_intake",
            "paper_id": pid,
            "reviewed_by": req.reviewed_by.strip(),
            "draft": req.model_dump(),
            "final": entry,
        })
    except OSError as exc:  # 审计失败不回滚入库，但如实告知
        return {
            "paper_id": pid,
            "entry": entry,
            "url": url,
            "in_training": False,
            "graphrag_indexed": False,
            "audit_written": False,
            "message": f"已入库，但审计流水写入失败：{type(exc).__name__}: {exc}",
        }
    return {
        "paper_id": pid,
        "entry": entry,
        "url": url,
        "in_training": False,
        "graphrag_indexed": False,
        "audit_written": True,
        "message": "已入库（仅文献库，未入训练集与 GraphRAG 图，检索层后续再接）",
    }
