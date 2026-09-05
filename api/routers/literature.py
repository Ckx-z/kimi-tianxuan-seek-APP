"""文献录入路由：Crossref 查询生成待审核草稿 + 审核确认入库 + 图谱（v1.7.0）。

只进文献库（overlay：用户库 user_data_root/literature/paper_titles.json 优先，
源码态为 data/paper_titles.json）与审计流水（user_data_root/literature/
literature_intake.jsonl）；不入训练集、不入 GraphRAG 图（confirm 响应注明
graphrag_indexed:false）。

图谱（需求三）：structure / spectra / mechanism 三类，文件与索引都落
user_data_root/literature/（见 src/literature/figures.py）。
"""

from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from ..schemas import (LiteratureConfirm, LiteratureFigureFromSmiles,
                       LiteratureFigureUpdate, LiteratureLookup)

router = APIRouter(prefix="/api/literature", tags=["literature"])


def _resolver():
    from literature import resolver
    return resolver


def _crossref():
    from literature import crossref
    return crossref


def _pdf_extract():
    from literature import pdf_extract
    return pdf_extract


def _figures():
    from literature import figures
    return figures


def _titles_module():
    from references import titles
    return titles


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


@router.post("/extract-pdf")
def extract_pdf(file: UploadFile = File(...)):
    """上传文献 PDF → LLM 提取元数据 → 与 lookup 相同的「待审核草稿」。

    Crossref 查不到/网络不通时的录入通道。source 标 "pdf-llm" 并附带
    pdf_filename；≤20MB；无文本层（扫描件）→ 422；LLM 未配置 → 503；
    LLM 调用失败/返回无法解析 → 502；非 PDF/损坏 → 400。
    """
    pe = _pdf_extract()
    filename = (file.filename or "").strip()
    if filename and not filename.lower().endswith(".pdf"):
        raise HTTPException(400, "请上传 PDF 文件")
    data = file.file.read()
    if not data:
        raise HTTPException(400, "上传文件为空")
    if len(data) > pe.MAX_PDF_BYTES:
        raise HTTPException(
            413,
            f"PDF 超过 {pe.MAX_PDF_BYTES // (1024 * 1024)}MB 上限，请压缩后重试")
    try:
        draft = pe.draft_from_pdf(data, pdf_filename=filename)
    except pe.PdfExtractError as exc:
        raise HTTPException(400, str(exc))
    except pe.PdfNoTextError as exc:
        raise HTTPException(422, str(exc))
    except pe.LLMNotConfiguredError as exc:
        raise HTTPException(503, str(exc))
    except pe.LLMExtractError as exc:
        raise HTTPException(502, str(exc))
    return {"draft": _mark_existing(draft)}


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


# ---------------------------------------------------------------------------
# 文献库列表 + 图谱（v1.7.0，需求三）
# ---------------------------------------------------------------------------

@router.get("/papers")
def list_papers():
    """文献库条目列表（paper_id 升序），供图谱关联选择。"""
    t = _titles_module()
    papers = t._load()
    out = []
    for pid in sorted(papers, key=lambda k: (str(k).isdigit(), int(k) if str(k).isdigit() else 0, str(k))):
        entry = papers.get(pid)
        if not isinstance(entry, dict):
            continue
        out.append({
            "paper_id": str(pid),
            "title": str(entry.get("title") or ""),
            "doi": str(entry.get("doi") or ""),
            "journal": str(entry.get("journal") or ""),
            "year": entry.get("year"),
        })
    return {"papers": out, "count": len(out)}


@router.post("/figures/from-smiles", status_code=201)
def figure_from_smiles(req: LiteratureFigureFromSmiles):
    """SMILES → RDKit 2D 结构图（structure 类）入库。"""
    f = _figures()
    try:
        rec = f.add_structure_from_smiles(req.paper_id, req.smiles, req.caption or "")
    except f.FigureError as exc:
        raise HTTPException(400, str(exc))
    return rec


@router.post("/{paper_id}/figures", status_code=201)
async def upload_figure(
    paper_id: str,
    file: UploadFile = File(...),
    figure_type: str = Form(...),
    caption: str = Form(""),
    tags: str = Form(""),
    meta_json: str | None = Form(None),
):
    """上传图谱（PNG/JPG/SVG/WebP ≤20MB）+ 标注入库。tags 逗号分隔。"""
    f = _figures()
    filename = (file.filename or "").strip()
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in f.ALLOWED_EXTS:
        raise HTTPException(400, f"仅支持图片格式 {sorted(f.ALLOWED_EXTS)}")
    data = await file.read()
    meta: dict = {}
    if meta_json:
        try:
            meta = json.loads(meta_json or "{}")
            if not isinstance(meta, dict):
                raise ValueError
        except Exception:
            raise HTTPException(400, "meta_json 必须是 JSON 对象")
    try:
        return f.add_figure(
            paper_id, figure_type, caption,
            [t.strip() for t in (tags or "").split(",") if t.strip()],
            meta, ext, data)
    except f.FigureError as exc:
        raise HTTPException(400, str(exc))


@router.get("/figures")
def list_figures(paper_id: str | None = None,
                 figure_type: str | None = None,
                 tag: str | None = None):
    """图谱筛选列表（paper_id / 类型 / 标签），created_at 倒序。"""
    f = _figures()
    return {"figures": f.list_figures(paper_id, figure_type, tag)}


@router.get("/figures/{fig_id}")
def figure_detail(fig_id: str):
    """图谱元数据（含 score_note / tags / meta）。"""
    f = _figures()
    rec = f.get_figure(fig_id)
    if rec is None:
        raise HTTPException(404, f"图谱不存在: {fig_id}")
    return rec


@router.get("/figures/{fig_id}/file")
def figure_file(fig_id: str):
    """图谱原文件（图片响应）。"""
    f = _figures()
    rec = f.get_figure(fig_id)
    if rec is None:
        raise HTTPException(404, f"图谱不存在: {fig_id}")
    path = f.figure_file_path(rec)
    if not path.is_file():
        raise HTTPException(404, f"图谱文件缺失: {fig_id}")
    return FileResponse(path, media_type=rec.get("mime") or "application/octet-stream")


@router.patch("/figures/{fig_id}")
def update_figure(fig_id: str, req: LiteratureFigureUpdate):
    """更新图谱标注（caption/tags/meta/score_note，至少一项）。"""
    f = _figures()
    body = req.model_dump(exclude_unset=True)
    if not body:
        raise HTTPException(400, "至少提供一项要更新的字段")
    try:
        rec = f.update_figure(
            fig_id, caption=body.get("caption"), tags=body.get("tags"),
            meta=body.get("meta"), score_note=body.get("score_note"))
    except f.FigureError as exc:
        raise HTTPException(400, str(exc))
    if rec is None:
        raise HTTPException(404, f"图谱不存在: {fig_id}")
    return rec


@router.delete("/figures/{fig_id}")
def delete_figure(fig_id: str):
    """删除图谱（文件 + 索引同步移除）。"""
    f = _figures()
    if not f.delete_figure(fig_id):
        raise HTTPException(404, f"图谱不存在: {fig_id}")
    return {"deleted": True, "fig_id": fig_id}
