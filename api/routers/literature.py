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
import re
from datetime import datetime

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from ..schemas import (LiteratureConfirm, LiteratureEntriesBatch,
                       LiteratureEntryUpdate, LiteratureFigureFromSmiles,
                       LiteratureFigureUpdate, LiteratureFiguresAnalyze,
                       LiteratureFiguresImport,
                       LiteratureLlmSettingsUpdate,
                       LiteratureLookup, LiteraturePaperUpdate)

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
    # v1.9.3（第 7 点）：新文献立即写入本机知识图谱的「文献节点」
    # （node_type=literature，标题/摘要可被助手 query_graphrag 检索命中）；
    # 失败不回滚入库，如实标注 graph_indexed=false 可重试。
    graph_indexed = False
    try:
        from literature import graph_ingest
        graph_indexed = bool(graph_ingest.upsert_paper_node(pid, entry))
    except Exception as exc:  # pragma: no cover
        logger.warning("文献节点入图失败（已入库，可重试）: %s", exc)
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
            "graphrag_indexed": graph_indexed,
            "audit_written": False,
            "message": f"已入库，但审计流水写入失败：{type(exc).__name__}: {exc}",
        }
    return {
        "paper_id": pid,
        "entry": entry,
        "url": url,
        "in_training": False,
        "graphrag_indexed": graph_indexed,
        "audit_written": True,
        "message": "已入库并写入本机知识图谱（文献节点可被助手检索）；"
                   "未入训练集。上传全文「补解析」后其结构化条目与实验组"
                   "关系会继续并入图谱。",
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


@router.get("/papers/{paper_id}")
def paper_detail(paper_id: str):
    """文献完整元数据（标题/作者/期刊/年份/DOI/摘要/来源），供卡片展示。"""
    t = _titles_module()
    entry = t.resolve_entry(paper_id)
    if entry is None:
        raise HTTPException(404, f"文献不存在: {paper_id}")
    return {
        "paper_id": str(paper_id),
        "title": str(entry.get("title") or ""),
        "authors": entry.get("authors") or [],
        "journal": str(entry.get("journal") or ""),
        "year": entry.get("year"),
        "doi": str(entry.get("doi") or ""),
        "url": str(entry.get("url") or "") or (
            f"https://doi.org/{entry['doi']}" if entry.get("doi") else ""),
        "abstract": entry.get("abstract") or None,
        "source": str(entry.get("source") or ""),
        "added_at": str(entry.get("added_at") or ""),
    }


@router.patch("/papers/{paper_id}")
def update_paper(paper_id: str, req: LiteraturePaperUpdate):
    """回填文献级元数据（补解析 LLM 提取结果；默认只补空字段）。

    返回 updated / skipped 列表，便于前端如实提示「哪些字段已存在被保留」。
    """
    r = _resolver()
    fields = req.model_dump(exclude_none=True)
    only_empty = bool(fields.pop("only_empty", True))
    res = r.update_paper_fields(paper_id, fields, only_empty=only_empty)
    if res.get("missing"):
        raise HTTPException(404, f"文献不存在: {paper_id}")
    # v1.9.3：元数据变化同步到知识图谱的文献节点（标题/摘要影响检索命中）
    try:
        from literature import graph_ingest
        graph_ingest.upsert_paper_node(paper_id, res.get("entry") or {})
    except Exception as exc:  # pragma: no cover
        logger.warning("文献节点更新失败（文献库已更新）: %s", exc)
    updated = res.get("updated") or []
    skipped = res.get("skipped") or []
    msg = (f"已回填 {len(updated)} 个字段"
           + (f"（{', '.join(updated)}）" if updated else "")
           + (f"；{len(skipped)} 个字段已有值被保留" if skipped else ""))
    return {**res, "message": msg}


@router.get("/figure-staging/{staged_id}/file")
def staged_figure_file(staged_id: str):
    """预览/下载解析时抽取到、尚未入库的候选图。"""
    try:
        from literature import pdf_figures
    except ImportError:  # pragma: no cover
        from src.literature import pdf_figures  # type: ignore
    hit = pdf_figures.get_staged(staged_id)
    if hit is None:
        raise HTTPException(404, "候选图不存在或暂存已过期（重新解析即可）")
    path, meta = hit
    return FileResponse(path, filename=f"{staged_id}{path.suffix}",
                        media_type="image/png" if path.suffix == ".png"
                        else "image/jpeg")


@router.delete("/figure-staging/{staged_id}")
def discard_staged_figure(staged_id: str):
    """丢弃单个候选图（不入图谱）。"""
    try:
        from literature import pdf_figures
    except ImportError:  # pragma: no cover
        from src.literature import pdf_figures  # type: ignore
    if not pdf_figures.discard_staged(staged_id):
        raise HTTPException(404, "候选图不存在或已清理")
    return {"discarded": True, "staged_id": staged_id}


@router.post("/{paper_id}/figures/import", status_code=201)
def import_figures(paper_id: str, req: LiteratureFiguresImport):
    """把解析时抽取的候选图（staged_ids）正式写入文献图谱。

    - 复用 `figures.add_figure`（按 figure_type/caption/tags/meta 落库）；
    - 顺带做**条目↔图关联**：图注含 `Fig. 3` / `图 3` 时，若某条目 evidence 提到同一
      图号，则把该 figure_id 追加进条目的 `figure_ids`（`knowledge.update_entry`）；
    - 返回 {imported, skipped, figure_ids, linked_entries}。
    """
    r = _resolver()
    if r.resolve_paper(paper_id) is None:
        raise HTTPException(404, f"文献不存在: {paper_id}")
    try:
        from literature import pdf_figures
    except ImportError:  # pragma: no cover
        from src.literature import pdf_figures  # type: ignore
    res = pdf_figures.import_staged(paper_id, list(req.staged_ids or []))
    if not res["imported"] and res["skipped"]:
        raise HTTPException(400, res["skipped"][0]["reason"])

    # 条目 ↔ 图 关联（尽力而为，失败不影响已入库的图）
    linked = 0
    try:
        from literature import knowledge as knowledge_mod
        entries = knowledge_mod.list_entries(paper_id=paper_id)
        fig_by_label: dict[str, str] = {}
        for rec in res["imported"]:
            label = str((rec.get("meta") or {}).get("caption_label") or "")
            m = re.search(r"([0-9]{1,2})", label)
            if m:
                fig_by_label[m.group(1)] = rec["fig_id"]
        if fig_by_label:
            for entry in entries:
                evidence = str(entry.get("evidence") or "")
                ids = list(entry.get("figure_ids") or [])
                for num, fig_id in fig_by_label.items():
                    if fig_id in ids:
                        continue
                    if re.search(rf"(?:fig(?:ure)?\.?|图|scheme|表)\s*{num}\b",
                                 evidence, re.IGNORECASE):
                        ids.append(fig_id)
                if ids != list(entry.get("figure_ids") or []):
                    updated = knowledge_mod.update_entry(
                        entry["entry_id"], {**entry, "figure_ids": ids})
                    if updated:
                        linked += 1
    except Exception as exc:  # pragma: no cover
        logger.warning("条目↔图关联失败（图已入库）: %s", exc)

    return {**res, "linked_entries": linked,
            "count": len(res["imported"])}


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

MAX_PARSE_PDF_BYTES = 20 * 1024 * 1024   # 补解析 PDF 上限
MIN_PDF_CHARS_PER_PAGE = 50              # 低于此值视为无文本层（扫描件）


def _attachments():
    from literature import attachments
    return attachments


def _merge_entries(chunk_results: list[dict]) -> list[dict]:
    """多来源（主文 + 各 SI）解析结果合并去重。

    去重键 = (group_id, kind, ald_smiles, amine_smiles, technique,
    evidence 前 80 字)；同一条证据在多份附件里重复出现只留一条。
    每条带 `source_file`（如「[SI 1] xxx.pdf」）与 evidence 前缀，
    保证入库后能看出证据出自主文还是补充信息。
    """
    seen: set[tuple] = set()
    merged: list[dict] = []
    for res in chunk_results:
        label = str(res.get("label") or "")
        for entry in (res.get("entries") or []):
            if not isinstance(entry, dict):
                continue
            key = (str(entry.get("group_id")), str(entry.get("kind")),
                   str(entry.get("ald_smiles") or ""),
                   str(entry.get("amine_smiles") or ""),
                   str(entry.get("technique") or ""),
                   str(entry.get("evidence") or "")[:80])
            if key in seen:
                continue
            seen.add(key)
            item = dict(entry)
            if label and not str(item.get("source_file") or "").strip():
                item["source_file"] = label
                evidence = str(item.get("evidence") or "").strip()
                item["evidence"] = f"{label} {evidence}".strip()
            merged.append(item)
    merged.sort(key=lambda e: (str(e.get("source_file") or ""),
                               str(e.get("group_id") or "")))
    return merged


@router.post("/{paper_id}/attachments", status_code=201)
async def upload_attachments(
    paper_id: str,
    files: list[UploadFile] = File(...),
    role: str = Form(""),
    roles: str = Form(""),
):
    """上传文献附件（主文 + 补充信息 SI，可一次多份）。

    - 仅 PDF、单文件 ≤20MB、单文献 ≤8 个附件、sha1 内容去重（重复上传不占盘）；
    - 角色分配：`roles`（逗号分隔，逐文件）优先 > `role`（整批统一）>
      自动规则（该文献还没有主文时第 1 份为 main，其余为 si）。
    返回 {uploaded: [...], errors: [{filename, message}], count}。
    """
    r = _resolver()
    if r.resolve_paper(paper_id) is None:
        raise HTTPException(404, f"文献不存在: {paper_id}")
    att = _attachments()
    role_list = [x.strip().lower() for x in (roles or "").split(",") if x.strip()]
    only = (role or "").strip().lower()
    has_main = any(i.get("role") == "main" for i in att.list_pdfs(paper_id))
    uploaded: list[dict] = []
    errors: list[dict] = []
    for idx, f in enumerate(files):
        filename = (f.filename or "").strip() or f"document{idx + 1}.pdf"
        data = await f.read()
        if idx < len(role_list):
            wanted = role_list[idx]
        elif only:
            wanted = only
        elif idx == 0 and not has_main:
            wanted = "main"
        else:
            wanted = "si"
        try:
            meta = att.save_pdf(paper_id, filename, data, role=wanted)
        except att.AttachmentError as exc:
            errors.append({"filename": filename, "message": str(exc)})
            continue
        uploaded.append(meta)
    if not uploaded and errors:
        raise HTTPException(400, errors[0]["message"])
    return {"uploaded": uploaded, "errors": errors, "count": len(uploaded)}


@router.get("/{paper_id}/attachments")
def list_attachments(paper_id: str):
    """某文献的附件列表（主文在前，SI 按上传顺序）。"""
    att = _attachments()
    items = att.list_pdfs(paper_id)
    items.sort(key=lambda i: (0 if i.get("role") == "main" else 1,
                              str(i.get("uploaded_at") or "")))
    return {"attachments": items, "count": len(items),
            "max_per_paper": att.MAX_PDFS_PER_PAPER}


@router.get("/attachments/{file_id}/file")
def download_attachment(file_id: str):
    """下载/预览附件 PDF。"""
    att = _attachments()
    hit = att.get_pdf(file_id)
    if hit is None:
        raise HTTPException(404, "附件不存在或文件已丢失")
    path, meta = hit
    return FileResponse(path, media_type="application/pdf",
                        filename=meta.get("filename") or path.name)


@router.patch("/attachments/{file_id}")
def update_attachment(file_id: str, role: str = Form(...)):
    """切换附件角色（main / si）。"""
    att = _attachments()
    try:
        meta = att.update_meta(file_id, role=(role or "").strip().lower())
    except att.AttachmentError as exc:
        raise HTTPException(400, str(exc))
    if meta is None:
        raise HTTPException(404, "附件不存在")
    return meta


@router.delete("/attachments/{file_id}")
def delete_attachment(file_id: str):
    """删除附件（索引 + 文件）。"""
    att = _attachments()
    if not att.delete_pdf(file_id):
        raise HTTPException(404, "附件不存在")
    return {"deleted": True, "file_id": file_id}


@router.post("/{paper_id}/parse")
async def parse_paper(paper_id: str,
                      file: UploadFile | None = File(None),
                      files: list[UploadFile] | None = File(None),
                      text: str | None = Form(None),
                      with_meta: bool = Form(True),
                      use_stored: bool = Form(False),
                      file_ids: str | None = Form(None),
                      store_files: bool = Form(True),
                      extract_figures: bool = Form(True)):
    """全维度解析：主文 +（多份）补充信息 SI → LLM 结构化条目预览。

    - 三种取材方式：① `files`（可多份，自动留存到附件库；`store_files=False`
      则只解析不留存）② `file`（兼容旧的单文件调用）③ `use_stored=true`
      或 `file_ids`（复用已上传附件，不必重复传大文件）④ `text` 全文文本；
    - 主文与 SI **分别解析后合并去重**（避免跨文件截断丢信息），条目带
      `source_file`（[主文]/[SI n] 文件名）便于核对证据出处；
    - `with_meta=True` 时附 `paper_meta`（文献级元数据，取自主文）；
    - `extract_figures=True`（默认）时自动抽取文献图（内嵌位图 + 矢量图页渲染兜底），
      候选图暂存并随响应返回 `figures`，前端勾选后调
      `POST /{paper_id}/figures/import` 正式入文献图谱；
    - PDF 无文本层（扫描件）→ 422 并提示；单文件 ≤20MB。
    """
    r = _resolver()
    if r.resolve_paper(paper_id) is None:
        raise HTTPException(404, f"文献不存在: {paper_id}")
    att = _attachments()
    ext = _llm_extract()

    # ---------- 取材：解析目标（filename, role, label, text） ----------
    targets: list[dict] = []
    scanned: list[str] = []
    saved: list[dict] = []
    save_errors: list[dict] = []
    pending: list[dict] = []          # 新上传（内存，无论是否留存都参与解析）

    incoming = list(files or [])
    if file is not None:
        incoming.append(file)

    def _role_for(idx: int) -> str:
        existing = att.list_pdfs(paper_id)
        has_main = any(i.get("role") == "main" for i in existing)
        if idx == 0 and not has_main:
            return "main"
        return "si"

    # 1) 新上传文件：校验 → （可选）留存到附件库 → 进内存待解析
    for idx, f in enumerate(incoming):
        filename = (f.filename or "").strip() or f"document{idx + 1}.pdf"
        data = await f.read()
        if not data:
            raise HTTPException(400, f"上传文件为空：{filename}")
        if len(data) > MAX_PARSE_PDF_BYTES:
            raise HTTPException(
                413,
                f"{filename} 超过 {MAX_PARSE_PDF_BYTES // (1024 * 1024)}MB 上限")
        wanted = _role_for(idx)
        if store_files:
            try:
                saved.append(att.save_pdf(paper_id, filename, data,
                                          role=wanted))
            except att.AttachmentError as exc:
                save_errors.append({"filename": filename,
                                    "message": str(exc)})
        pending.append({"filename": filename, "data": data, "role": wanted})

    # 2) 已存附件（use_stored / file_ids 显式指定；无新输入时自动复用）
    use_ids = [x.strip() for x in (file_ids or "").split(",") if x.strip()]
    want_stored = bool(use_stored or use_ids
                       or (not incoming and not (text or "").strip()))
    si_n = 0
    if want_stored:
        stored = att.list_pdfs(paper_id)
        if use_ids:
            stored = [i for i in stored if i.get("file_id") in set(use_ids)]
        stored.sort(key=lambda i: (0 if i.get("role") == "main" else 1,
                                   str(i.get("uploaded_at") or "")))
        for item in stored:
            path = att._path_of(paper_id, item.get("file_id"),
                                item.get("filename") or "")
            if not path.is_file():
                continue
            try:
                body, pages, chars = att.pdf_text(path)
            except att.AttachmentError as exc:
                raise HTTPException(400, f"{item.get('filename')}：{exc}")
            if pages and chars / pages < MIN_PDF_CHARS_PER_PAGE:
                scanned.append(str(item.get("filename") or ""))
                continue
            if item.get("role") == "main":
                label = f"[主文 {item.get('filename')}]"
            else:
                si_n += 1
                label = f"[SI {si_n} {item.get('filename')}]"
            targets.append({"filename": item.get("filename"),
                            "role": item.get("role") or "main",
                            "label": label, "text": body, "pages": pages,
                            "path": path})

    # 3) 新上传文件（内存直读）→ 解析目标
    for item in pending:
        try:
            body, pages, chars = att.pdf_text_from_bytes(item["data"])
        except att.AttachmentError as exc:
            raise HTTPException(400, f"{item['filename']}：{exc}")
        if pages and chars / pages < MIN_PDF_CHARS_PER_PAGE:
            scanned.append(item["filename"])
            continue
        if item["role"] == "main":
            label = f"[主文 {item['filename']}]"
        else:
            si_n += 1
            label = f"[SI {si_n} {item['filename']}]"
        targets.append({"filename": item["filename"], "role": item["role"],
                        "label": label, "text": body, "pages": pages,
                        "data": item["data"]})

    # 4) 粘贴的全文文本
    body_text = (text or "").strip()
    if body_text and not targets:
        targets.append({"filename": "（粘贴的全文文本）", "role": "text",
                        "label": "[全文文本]", "text": body_text,
                        "pages": 0})

    if not targets:
        if scanned:
            raise HTTPException(
                422,
                "以下 PDF 无可提取文本层（疑似扫描件/纯图片版）："
                + "、".join(scanned)
                + "。请改用「用全文文本解析」，或等待后续版本的 OCR / "
                  "视觉模型支持")
        raise HTTPException(
            400, "请提供 PDF 文件（可多份，含补充信息）、已存附件或 text 全文")

    # ---------- 逐来源解析后合并 ----------
    chunk_results: list[dict] = []
    for tgt in targets:
        res = ext.parse_text(tgt["text"])
        chunk_results.append({"label": tgt["label"], **res})
    entries = _merge_entries(chunk_results)
    # v1.9.3：逐条试校验标注（前端默认只勾选合法条目，避免原子入库整批失败）
    try:
        from literature import knowledge as knowledge_mod
        entries = knowledge_mod.annotate_entries(entries)
    except Exception as exc:  # pragma: no cover - 标注失败不影响解析结果
        logger.warning("条目试校验标注失败（已跳过）: %s", exc)
    invalid_n = sum(1 for e in entries if e.get("valid") is False)
    llm_used = any(bool(r.get("llm_used")) for r in chunk_results)
    notes = "；".join(
        f"{r['label']} {r.get('note')}" for r in chunk_results if r.get("note"))
    fail_segments = sum(int((r.get("segments") or {}).get("failed") or 0)
                        for r in chunk_results)
    segments = sum(int((r.get("segments") or {}).get("total") or 0)
                   for r in chunk_results)

    main_text = next((t["text"] for t in targets if t["role"] == "main"),
                     targets[0]["text"] if targets else "")
    total_chars = sum(len(t["text"]) for t in targets)

    # ---------- 文献图抽取（v1.9.4 方案 A）：内嵌位图 + 矢量图页渲染兜底 ----------
    figure_candidates: list[dict] = []
    figure_errors: list[str] = []
    if extract_figures:
        try:
            from literature import pdf_figures
        except ImportError:  # pragma: no cover
            from src.literature import pdf_figures  # type: ignore
        for tgt in targets:
            if tgt.get("role") == "text":
                continue
            try:
                if tgt.get("path") is not None:
                    cands = pdf_figures.extract_candidates(path=tgt["path"])
                elif tgt.get("data"):
                    cands = pdf_figures.extract_candidates(data=tgt["data"])
                else:
                    continue
            except Exception as exc:
                logger.warning("文献图抽取失败 %s: %s", tgt.get("filename"), exc)
                figure_errors.append(f"{tgt.get('filename')}：{exc}")
                continue
            for cand in cands:
                cand["source_file"] = tgt.get("filename")
            figure_candidates.extend(cands)
        figure_candidates = figure_candidates[:pdf_figures.MAX_PER_PAPER]
    staged_figures = []
    if figure_candidates:
        try:
            staged_figures = pdf_figures.stage_candidates(paper_id,
                                                           figure_candidates)
        except Exception as exc:  # pragma: no cover
            logger.warning("候选图暂存失败（已跳过）: %s", exc)
            figure_errors.append(f"暂存失败：{exc}")

    result = {
        "llm_used": llm_used,
        "entries": entries,
        "valid_count": len(entries) - invalid_n,
        "invalid_count": invalid_n,
        "note": (notes or "解析完成")
                + (f"；合并后 {len(entries)} 条" if len(targets) > 1 else "")
                + (f"；抽取到 {len(staged_figures)} 张文献图"
                   if staged_figures else ""),
        "segments": {"total": segments, "failed": fail_segments},
        "sources": [{"filename": t["filename"], "role": t["role"],
                     "pages": t.get("pages") or 0,
                     "chars": len(t["text"].strip())} for t in targets],
        "figures": staged_figures,
        "figure_counts": {
            "total": len(staged_figures),
            "embedded": sum(1 for f in staged_figures if f.get("kind") == "embedded"),
            "page_render": sum(1 for f in staged_figures
                               if f.get("kind") == "page_render"),
        },
        "figure_errors": figure_errors,
        "saved": saved,
        "save_errors": save_errors,
        "scanned": scanned,
        "chars": total_chars,
        "pages": sum(int(t.get("pages") or 0) for t in targets),
    }
    if with_meta and main_text:
        meta = ext.extract_paper_meta(main_text)
        result["paper_meta"] = meta.get("meta") or {}
        result["meta_note"] = meta.get("note") or ""
    return {"paper_id": paper_id, **result}


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
    """文献解析 LLM 设置（key 只回显掩码）+ 视觉读图状态。"""
    try:
        from literature import vision
    except ImportError:  # pragma: no cover
        from src.literature import vision  # type: ignore
    return {**_llm_extract().get_settings(), "vision_status": vision.status()}


@router.put("/llm-settings")
def put_llm_settings(req: LiteratureLlmSettingsUpdate):
    """保存文献解析 LLM 设置（只改传入字段，含视觉读图 4 个字段）。"""
    body = req.model_dump(exclude_unset=True)
    return _llm_extract().save_settings(
        enabled=body.get("enabled"), base_url=body.get("base_url"),
        api_key=body.get("api_key"), model=body.get("model"),
        embedding_provider=body.get("embedding_provider"),
        embedding_model=body.get("embedding_model"),
        embedding_api_key=body.get("embedding_api_key"),
        vision_enabled=body.get("vision_enabled"),
        vision_base_url=body.get("vision_base_url"),
        vision_api_key=body.get("vision_api_key"),
        vision_model=body.get("vision_model"))


@router.post("/llm-settings/test")
def test_llm_settings():
    """测试文献解析 LLM 连接。"""
    return _llm_extract().test_connection()


@router.post("/{paper_id}/figures/analyze")
def analyze_figures(paper_id: str, req: LiteratureFiguresAnalyze):
    """视觉读图（v1.9.4 方案 B）：对暂存候选图调用视觉模型读描述与数值。

    - 未启用/未配置视觉模型 → 400 且提示到设置页开启（**方案 A 不受影响**：
      不开启时图照样抽取入库，只是不读图内数值）；
    - 单次默认最多 6 张（`max_figures` 控制成本）；
    - 返回 `results`（每张图的描述/数值/置信度）与 `entries`（可直接入预览勾选的条目）。
    """
    r = _resolver()
    if r.resolve_paper(paper_id) is None:
        raise HTTPException(404, f"文献不存在: {paper_id}")
    try:
        from literature import pdf_figures, vision
    except ImportError:  # pragma: no cover
        from src.literature import pdf_figures, vision  # type: ignore
    if not vision.is_enabled():
        raise HTTPException(
            400, "视觉读图未启用：请到「设置 → 文献解析 LLM → 视觉读图」开启并填写"
                 "支持图片输入的模型（关闭时不影响文献图抽取入库）")
    ids = list(req.staged_ids or [])[:max(1, int(req.max_figures or 6))]
    results: list[dict] = []
    entries: list[dict] = []
    for sid in ids:
        hit = pdf_figures.get_staged(sid)
        if hit is None:
            results.append({"staged_id": sid, "ok": False,
                            "error": "暂存已过期或不存在"})
            continue
        path, meta = hit
        mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        try:
            analysis = vision.analyze_image(path.read_bytes(), mime=mime,
                                            hint=req.hint or "")
        except Exception as exc:  # pragma: no cover - 兜底不炸整个请求
            analysis = {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                        "metrics": [], "description": "", "technique": "",
                        "confidence": "", "notes": ""}
        results.append({"staged_id": sid,
                        "page": meta.get("page"),
                        "caption_label": meta.get("caption_label"),
                        **analysis})
        entry = vision.propose_entry(meta, analysis)
        if entry:
            entries.append(entry)
    try:
        from literature import knowledge as knowledge_mod
        entries = knowledge_mod.annotate_entries(entries)
    except Exception as exc:  # pragma: no cover
        logger.warning("视觉条目试校验标注失败（已跳过）: %s", exc)
    ok_n = sum(1 for x in results if x.get("ok"))
    return {
        "paper_id": paper_id,
        "analyzed": len(results),
        "ok_count": ok_n,
        "metric_total": sum(len(x.get("metrics") or []) for x in results),
        "results": results,
        "entries": entries,
        "vision": vision.status(),
        "note": (f"视觉读图完成：{ok_n}/{len(results)} 张成功，"
                 f"提出 {len(entries)} 条待勾选条目"),
    }


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


@router.post("/entries/import-from-graph")
def import_entries_from_graph():
    """把随包知识图谱的历史反应节点导入为结构化条目（幂等）。

    旧图谱（graph_v2.pkl）含 6197 个反应节点（单体/条件/成膜结论），
    导入后各文献卡片即可看到历史结构化信息；可重复调用，已导入的跳过。
    """
    k = _knowledge()
    try:
        stats = k.import_from_graph()
    except Exception as exc:
        raise HTTPException(500, f"图谱导入失败：{type(exc).__name__}: {exc}")
    return stats
