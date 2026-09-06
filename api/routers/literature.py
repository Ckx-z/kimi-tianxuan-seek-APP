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
import logging
from datetime import datetime

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from ..schemas import (LiteratureConfirm, LiteratureEntriesBatch,
                       LiteratureEntryUpdate, LiteratureFigureFromSmiles,
                       LiteratureFigureUpdate, LiteratureLlmSettingsUpdate,
                       LiteratureLookup)

logger = logging.getLogger(__name__)

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


def _knowledge():
    from literature import knowledge
    return knowledge


def _llm_extract():
    from literature import llm_extract
    return llm_extract


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


# ---------------------------------------------------------------------------
# 科研知识库（v1.9.0）：结构化提取 / 条目库 / 文献解析 LLM 设置
# ---------------------------------------------------------------------------

@router.post("/{paper_id}/parse")
async def parse_paper(paper_id: str,
                      file: UploadFile | None = File(None),
                      text: str | None = Form(None)):
    """全维度解析：上传 PDF（或直接给全文文本）→ LLM 结构化条目预览。

    文献解析 LLM 未启用时降级为 SMILES 正则扫描（llm_used=false）。
    """
    r = _resolver()
    if r.resolve_paper(paper_id) is None:
        raise HTTPException(404, f"文献不存在: {paper_id}")
    body = (text or "").strip()
    if file is not None:
        data = await file.read()
        if not data:
            raise HTTPException(400, "上传文件为空")
        try:
            import fitz
            doc = fitz.open(stream=data, filetype="pdf")
            body = "\n".join(page.get_text() for page in doc)
            doc.close()
        except Exception as exc:
            raise HTTPException(400, f"PDF 解析失败：{exc}")
    if not body:
        raise HTTPException(400, "请提供 PDF 文件或 text 全文")
    ext = _llm_extract()
    return {"paper_id": paper_id, **ext.parse_text(body)}


@router.post("/{paper_id}/entries", status_code=201)
def add_entries(paper_id: str, req: LiteratureEntriesBatch):
    """审核后的结构化条目批量入库（原子：校验全部通过才写）。"""
    r = _resolver()
    if r.resolve_paper(paper_id) is None:
        raise HTTPException(404, f"文献不存在: {paper_id}")
    k = _knowledge()
    try:
        entries = k.add_entries(paper_id, req.entries)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    # 入图（组级同步；失败不阻塞入库：条目 graph_indexed=false，可重试）
    try:
        from literature import graph_ingest
        n_synced = graph_ingest.sync_groups(entries)
        for e in entries:
            k.mark_graph_indexed(e["entry_id"], True)
    except Exception as exc:  # pragma: no cover
        logger.warning("条目入图失败（已入库，可重试）: %s", exc)
        n_synced = 0
    # 向量化（off/失败跳过，不阻塞）
    try:
        from literature import embedding
        n_vec = embedding.sync_entries(entries)
    except Exception as exc:  # pragma: no cover
        logger.warning("条目向量化失败（已入库）: %s", exc)
        n_vec = 0
    return {"entries": entries, "count": len(entries),
            "graph_synced": n_synced, "embedded": n_vec}


@router.get("/{paper_id}/entries")
def paper_entries(paper_id: str):
    """某文献的条目（按 group_id 分组）。"""
    k = _knowledge()
    entries = k.list_entries(paper_id=paper_id)
    return {"entries": entries, "count": len(entries),
            "groups": k.group_by(entries)}


@router.get("/entries")
def search_entries(paper_id: str | None = None, kind: str | None = None,
                   technique: str | None = None, film_label: float | None = None,
                   metric: str | None = None, min: float | None = None,
                   max: float | None = None):
    """跨文献条目检索（含数值范围，如 metric=PLQY&min=20）。"""
    k = _knowledge()
    entries = k.list_entries(paper_id=paper_id, kind=kind, technique=technique,
                             film_label=film_label, metric=metric,
                             min_value=min, max_value=max)
    return {"entries": entries, "count": len(entries)}


@router.patch("/entries/{entry_id}")
def update_entry(entry_id: str, req: LiteratureEntryUpdate):
    """编辑条目（整体重校验后替换；旧组/新组分别同步侧车图）。"""
    k = _knowledge()
    old = k.get_entry(entry_id)
    if old is None:
        raise HTTPException(404, f"条目不存在: {entry_id}")
    try:
        rec = k.update_entry(entry_id, req.entry)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    try:
        from literature import graph_ingest
        graph_ingest.sync_group(str(old.get("paper_id")),
                                str(old.get("group_id")))
        graph_ingest.sync_group(str(rec.get("paper_id")),
                                str(rec.get("group_id")))
        k.mark_graph_indexed(entry_id, True)
    except Exception as exc:  # pragma: no cover
        logger.warning("条目改后同步入图失败: %s", exc)
    return rec


@router.delete("/entries/{entry_id}")
def delete_entry(entry_id: str):
    """删除条目（同步该组侧车图节点：组内无剩余条目则移除节点）。"""
    k = _knowledge()
    removed = k.delete_entry(entry_id)
    if removed is None:
        raise HTTPException(404, f"条目不存在: {entry_id}")
    try:
        from literature import graph_ingest
        graph_ingest.sync_group(str(removed.get("paper_id")),
                                str(removed.get("group_id")))
    except Exception as exc:  # pragma: no cover
        logger.warning("条目撤图失败（不影响删除）: %s", exc)
    try:
        from literature import embedding
        embedding.remove_entry(entry_id)
    except Exception as exc:  # pragma: no cover
        logger.warning("条目向量移除失败（不影响删除）: %s", exc)
    return {"deleted": True, "entry_id": entry_id}


@router.post("/entries/{entry_id}/to-gnn-feedback", status_code=201)
def entry_to_gnn_feedback(entry_id: str):
    """film_outcome 条目 → GNN 反馈队列（v1.8.0 机制）。"""
    k = _knowledge()
    rec = k.get_entry(entry_id)
    if rec is None:
        raise HTTPException(404, f"条目不存在: {entry_id}")
    if rec.get("kind") != "film_outcome":
        raise HTTPException(400, "仅 film_outcome 条目可转入 GNN 反馈")
    try:
        from src.predictor import gnn_feedback
    except ImportError:  # pragma: no cover
        from predictor import gnn_feedback  # type: ignore
    try:
        fb = gnn_feedback.submit(
            rec["ald_smiles"], rec["amine_smiles"],
            float(rec.get("film_label")),
            note=f"文献条目 {entry_id}：{rec.get('evidence') or ''}"[:300],
            source="literature_pdf")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return fb


@router.post("/entries/{entry_id}/to-dft")
def entry_to_dft(entry_id: str):
    """单体对条目 → DFT 页预填参数（/toolbox/dft?a=&b=）。"""
    k = _knowledge()
    rec = k.get_entry(entry_id)
    if rec is None:
        raise HTTPException(404, f"条目不存在: {entry_id}")
    if not rec.get("ald_smiles") or not rec.get("amine_smiles"):
        raise HTTPException(400, "该条目不含单体对，无法预填 DFT")
    from urllib.parse import quote
    return {
        "ald_smiles": rec["ald_smiles"],
        "amine_smiles": rec["amine_smiles"],
        "url": f"/toolbox/dft?a={quote(rec['ald_smiles'])}"
               f"&b={quote(rec['amine_smiles'])}",
    }


@router.get("/llm-settings")
def get_llm_settings():
    """文献解析 LLM 设置（key 只回显掩码）。"""
    return _llm_extract().get_settings()


@router.put("/llm-settings")
def put_llm_settings(req: LiteratureLlmSettingsUpdate):
    """保存文献解析 LLM 设置（只改传入字段）。"""
    body = req.model_dump(exclude_unset=True)
    return _llm_extract().save_settings(
        enabled=body.get("enabled"), base_url=body.get("base_url"),
        api_key=body.get("api_key"), model=body.get("model"),
        embedding_provider=body.get("embedding_provider"),
        embedding_model=body.get("embedding_model"),
        embedding_api_key=body.get("embedding_api_key"))


@router.post("/llm-settings/test")
def test_llm_settings():
    """测试文献解析 LLM 连接。"""
    return _llm_extract().test_connection()


@router.get("/embedding-status")
def embedding_status():
    """本地/在线 embedding 提供方可用性（设置页展示）。"""
    from literature import embedding
    return embedding.status()


@router.get("/entries/vector-search")
def vector_search(q: str, top_k: int = 5):
    """向量检索（embedding 关闭时返回空列表）。"""
    from literature import embedding
    top_k = max(1, min(int(top_k), 20))
    return {"entries": embedding.search(q, top_k=top_k)}
