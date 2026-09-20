"""科研文献库 API（v1.9.7 P1/P2，借鉴 Zotero local API 的参数设计）。

这一层是**新的三栏文献库 UI 的后端**：服务端检索（`q/limit/offset/sort/direction/
collection/tag`）+ 集合（树）/标签/笔记 + 跨文献数值检索 + 关系查询。

数据来源：`src/literature/library_db.py` 的 SQLite 单库（镜像既有落盘文件；
原文件仍是写入的事实来源，DB 可随时 `POST /api/library/rebuild` 重建）。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from ..schemas import (LibraryAnnotationCreate, LibraryAnnotationUpdate,
                       LibraryCollectionCreate, LibraryCollectionRename,
                       LibraryNoteCreate, LibraryNoteUpdate, LibraryPaperPatch,
                       LibraryTagSet, ReadingAskRequest, ReadingTranslateRequest)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/library", tags=["library"])


def _db():
    """惰性 import（沿用本项目对 literature 包的既有约定，PyInstaller 友好）。"""
    try:
        from literature import library_db
    except ImportError:  # pragma: no cover
        from src.literature import library_db  # type: ignore
    return library_db


def _open():
    db = _db()
    db.ensure_ready()
    return db, db.connect()


# ---------------------------------------------------------------- 状态 / 检索

@router.get("/status")
def library_status():
    """DB 状态：迁移计数、校验结果、各类条数、文件路径与体积。"""
    db, conn = _open()
    try:
        counts = {}
        row = conn.execute(
            "SELECT value_json FROM settings WHERE key='migrated_counts'").fetchone()
        if row is not None:
            try:
                counts = __import__("json").loads(row["value_json"])
            except Exception:
                counts = {}
        return {"ok": True, "counts": counts, "stats": db.stats(conn),
                "verify": db.verify_migration(conn)}
    finally:
        conn.close()


@router.post("/rebuild")
def library_rebuild():
    """用既有文件重建 DB（镜像层可随时重建；原文件不受影响）。"""
    db = _db()
    try:
        return {"ok": True, **db.rebuild(backup=True)}
    except Exception as exc:
        raise HTTPException(500, f"重建失败：{type(exc).__name__}: {exc}")


@router.get("/papers")
def library_papers(q: str = "", limit: int = Query(50, ge=1, le=500),
                   offset: int = Query(0, ge=0), sort: str = "paper_id",
                   direction: str = "desc", collection_id: str | None = None,
                   tag: str | None = None, source: str | None = None):
    """服务端检索文献（替代 `/papers` 全量返回 + 前端本地过滤）。"""
    db, conn = _open()
    try:
        return db.search_papers(conn, q=q, limit=limit, offset=offset, sort=sort,
                                direction=direction, collection_id=collection_id,
                                tag=tag, source=source)
    finally:
        conn.close()


@router.get("/papers/{paper_id}")
def library_paper(paper_id: str):
    db, conn = _open()
    try:
        rec = db.get_paper(conn, paper_id)
        if rec is None:
            raise HTTPException(404, f"文献不存在: {paper_id}")
        return rec
    finally:
        conn.close()


@router.patch("/papers/{paper_id}")
def library_patch_paper(paper_id: str, req: LibraryPaperPatch):
    """更新文献元数据（仅传字段生效；`null` 视为不改）。"""
    db, conn = _open()
    try:
        if db.get_paper(conn, paper_id) is None:
            raise HTTPException(404, f"文献不存在: {paper_id}")
        body = req.model_dump(exclude_unset=True, exclude_none=True)
        if not body:
            raise HTTPException(400, "没有需要更新的字段")
        if "authors" in body and isinstance(body["authors"], list):
            body["authors_json"] = __import__("json").dumps(
                body.pop("authors"), ensure_ascii=False)
        db.upsert_paper(conn, paper_id, body)
        return {"ok": True, "paper_id": paper_id,
                "paper": db.get_paper(conn, paper_id)}
    finally:
        conn.close()


# ---------------------------------------------------------------- 集合

@router.get("/collections")
def library_collections():
    """集合树（含默认集合与「未分类」计数）。"""
    db, conn = _open()
    try:
        return {"collections": db.list_collections(conn)}
    finally:
        conn.close()


@router.post("/collections", status_code=201)
def library_create_collection(req: LibraryCollectionCreate):
    db, conn = _open()
    try:
        return db.create_collection(conn, req.name, req.parent_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    finally:
        conn.close()


@router.patch("/collections/{collection_id}")
def library_rename_collection(collection_id: str, req: LibraryCollectionRename):
    db, conn = _open()
    try:
        out = db.rename_collection(conn, collection_id, req.name)
        if out is None:
            raise HTTPException(404, f"集合不存在: {collection_id}")
        return out
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    finally:
        conn.close()


@router.delete("/collections/{collection_id}")
def library_delete_collection(collection_id: str):
    db, conn = _open()
    try:
        if not db.delete_collection(conn, collection_id):
            raise HTTPException(404, f"集合不存在: {collection_id}")
        return {"ok": True, "collection_id": collection_id}
    finally:
        conn.close()


@router.post("/papers/{paper_id}/collections/{collection_id}")
def library_add_to_collection(paper_id: str, collection_id: str):
    db, conn = _open()
    try:
        if db.get_paper(conn, paper_id) is None:
            raise HTTPException(404, f"文献不存在: {paper_id}")
        db.assign_collection(conn, paper_id, collection_id, add=True)
        return {"ok": True, "paper_id": paper_id, "collection_id": collection_id}
    finally:
        conn.close()


@router.delete("/papers/{paper_id}/collections/{collection_id}")
def library_remove_from_collection(paper_id: str, collection_id: str):
    db, conn = _open()
    try:
        db.assign_collection(conn, paper_id, collection_id, add=False)
        return {"ok": True, "paper_id": paper_id, "collection_id": collection_id}
    finally:
        conn.close()


# ---------------------------------------------------------------- 标签

@router.get("/tags")
def library_tags():
    db, conn = _open()
    try:
        return {"tags": db.list_tags(conn)}
    finally:
        conn.close()


@router.post("/papers/{paper_id}/tags")
def library_add_tag(paper_id: str, req: LibraryTagSet):
    db, conn = _open()
    try:
        if db.get_paper(conn, paper_id) is None:
            raise HTTPException(404, f"文献不存在: {paper_id}")
        db.set_tag(conn, paper_id, req.tag, add=True, type_=req.type or "manual")
        return {"ok": True, "paper_id": paper_id, "tag": req.tag}
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    finally:
        conn.close()


@router.delete("/papers/{paper_id}/tags/{tag}")
def library_remove_tag(paper_id: str, tag: str):
    db, conn = _open()
    try:
        db.set_tag(conn, paper_id, tag, add=False)
        return {"ok": True, "paper_id": paper_id, "tag": tag}
    finally:
        conn.close()


# ---------------------------------------------------------------- 笔记

@router.get("/papers/{paper_id}/notes")
def library_notes(paper_id: str):
    db, conn = _open()
    try:
        return {"notes": db.list_notes(conn, paper_id)}
    finally:
        conn.close()


@router.post("/papers/{paper_id}/notes", status_code=201)
def library_add_note(paper_id: str, req: LibraryNoteCreate):
    db, conn = _open()
    try:
        return db.add_note(conn, paper_id, req.content_md, title=req.title or "",
                           source=req.source or "user")
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    finally:
        conn.close()


@router.patch("/notes/{note_id}")
def library_update_note(note_id: str, req: LibraryNoteUpdate):
    db, conn = _open()
    try:
        out = db.update_note(conn, note_id, title=req.title,
                             content_md=req.content_md)
        if out is None:
            raise HTTPException(404, f"笔记不存在: {note_id}")
        return out
    finally:
        conn.close()


@router.delete("/notes/{note_id}")
def library_delete_note(note_id: str):
    db, conn = _open()
    try:
        if not db.delete_note(conn, note_id):
            raise HTTPException(404, f"笔记不存在: {note_id}")
        return {"ok": True, "note_id": note_id}
    finally:
        conn.close()


# ---------------------------------------------------------------- 数值检索 / 关系

@router.get("/metrics")
def library_metrics(name: str = "", min_value: float | None = None,
                    max_value: float | None = None, technique: str | None = None,
                    limit: int = Query(100, ge=1, le=1000)):
    """跨文献数值检索（如「比表面积 > 2000」）。"""
    db, conn = _open()
    try:
        return {"metrics": db.search_metrics(conn, name=name, min_value=min_value,
                                             max_value=max_value,
                                             technique=technique, limit=limit)}
    finally:
        conn.close()


@router.get("/relations")
def library_relations(subject: str | None = None, object: str | None = None,
                      predicate: str | None = None):
    """关系查询（P3 的 cites/similar_to 将写入此处）。"""
    db, conn = _open()
    try:
        return {"relations": db.list_relations(conn, subject=subject,
                                               object_=object,
                                               predicate=predicate)}
    finally:
        conn.close()


# ---------------------------------------------------------------- 图谱扩展（P3）

def _graph_extend():
    try:
        from literature import graph_extend
    except ImportError:  # pragma: no cover
        from src.literature import graph_extend  # type: ignore
    return graph_extend


@router.get("/graph/stats")
def library_graph_stats():
    """图谱规模（节点/边数、按谓词分布）。"""
    _db, conn = _open()
    try:
        return _graph_extend().graph_stats(conn)
    finally:
        conn.close()


@router.get("/graph/neighbors/{paper_id}")
def library_graph_neighbors(paper_id: str):
    """某文献的引用 / 被引 / 相似（供 UI「关联」面板）。"""
    _db, conn = _open()
    try:
        return _graph_extend().neighbors(conn, paper_id)
    finally:
        conn.close()


@router.post("/graph/sync")
def library_graph_sync(paper_id: str | None = None,
                       kinds: str = "entities,citations,similar",
                       fetch: bool = True, limit: int = 50):
    """构建图谱关系（幂等）：

    - `entities`：从结构化条目抽 technique/metric/property 节点与边（本地、零成本）
    - `citations`：按 DOI 调 Crossref 取参考文献写 `cites` 边（需联网；失败跳过）
    - `similar`：向量近邻写 `similar_to` 边（标注 source=embedding，属算法推荐）
    """
    db, conn = _open()
    ge = _graph_extend()
    want = {k.strip() for k in (kinds or "").split(",") if k.strip()}
    try:
        if paper_id:
            targets = [str(paper_id)]
        else:
            rows = conn.execute(
                "SELECT paper_id FROM items WHERE deleted_at IS NULL"
                " ORDER BY added_at DESC LIMIT ?", (max(1, min(limit, 500)),)).fetchall()
            targets = [str(r["paper_id"]) for r in rows]
        out: dict = {"targets": len(targets), "entities": [], "citations": []}
        for pid in targets:
            if "entities" in want:
                out["entities"].append(ge.sync_entities(conn, pid))
            if "citations" in want:
                row = conn.execute("SELECT doi FROM items WHERE paper_id=?",
                                   (pid,)).fetchone()
                doi = (row["doi"] if row else "") or ""
                if doi:
                    out["citations"].append(
                        ge.sync_citations(conn, pid, doi, fetch=fetch))
        if "similar" in want:
            out["similar"] = ge.sync_similar(conn, paper_id=paper_id)
        out["stats"] = ge.graph_stats(conn)
        return out
    finally:
        conn.close()


@router.delete("/graph/source/{source}")
def library_graph_clear_source(source: str):
    """按来源撤销图谱边（如 crossref / embedding / entry）。"""
    db, conn = _open()
    try:
        removed = _graph_extend().clear_source(conn, source)
        return {"ok": True, "source": source, "removed": removed}
    finally:
        conn.close()


# ---------------------------------------------------------------- GNN 联动（P4）

def _gnn_bridge():
    try:
        from literature import gnn_bridge
    except ImportError:  # pragma: no cover
        from src.literature import gnn_bridge  # type: ignore
    return gnn_bridge


@router.get("/gnn/candidates")
def library_gnn_candidates(collection_id: str | None = None, tag: str | None = None,
                           paper_id: str | None = None, limit: int = 200):
    """可入 GNN 反馈队列的文献正样本（film_outcome 且 label=1.0，含醛/胺 SMILES）。"""
    _db, conn = _open()
    try:
        return {"candidates": _gnn_bridge().collect_candidates(
            conn, collection_id=collection_id, tag=tag, paper_id=paper_id,
            limit=limit)}
    finally:
        conn.close()


@router.post("/gnn/enqueue")
def library_gnn_enqueue(collection_id: str | None = None, tag: str | None = None,
                        paper_id: str | None = None, limit: int = 200,
                        dry_run: bool = False):
    """批量把文献正样本推入 GNN 反馈队列（幂等；dry_run 只预览）。

    权重分层：用户确认过的条目 weight=2.0，LLM 提取未复核 weight=1.0；
    每条反馈的 refs 带 `paper:<id>` / `entry:<id>` / `doi:<doi>` 以便溯源。
    """
    _db, conn = _open()
    try:
        return _gnn_bridge().enqueue(conn, collection_id=collection_id, tag=tag,
                                     paper_id=paper_id, limit=limit, dry_run=dry_run)
    finally:
        conn.close()


@router.get("/gnn/links")
def library_gnn_links(paper_id: str | None = None):
    """已入队反馈的溯源（不给 paper_id 时返回总览）。"""
    bridge = _gnn_bridge()
    _db, conn = _open()
    try:
        if paper_id:
            return {"paper_id": paper_id, "links": bridge.links_for_paper(conn, paper_id)}
        return bridge.summary(conn)
    finally:
        conn.close()


@router.post("/gnn/mark-used")
def library_gnn_mark_used(version: str, feedback_ids: str | None = None):
    """标记反馈已用于某个 GNN 版本（供 UI「已用于 GNN 第 N 版」徽标）。"""
    ids = [x.strip() for x in (feedback_ids or "").split(",") if x.strip()]
    _db, conn = _open()
    try:
        return {"ok": True, "version": version,
                "marked": _gnn_bridge().mark_used(conn, version, ids or None)}
    finally:
        conn.close()


# ---------------------------------------------------------------- Zotero 互操作（P5）

def _zotero_io():
    try:
        from literature import zotero_io
    except ImportError:  # pragma: no cover
        from src.literature import zotero_io  # type: ignore
    return zotero_io


@router.post("/zotero/import")
def library_zotero_import(limit: int = 100, start: int = 0, dry_run: bool = False):
    """从 **Zotero 本地 API** 导入条目（只读；不碰 zotero.sqlite）。

    需在 Zotero 设置 → Advanced 勾选允许本机应用通信，否则返回 400 并说明关卡。
    DOI 重复的条目自动跳过；导入的条目归入「Zotero 导入」集合、标签标为自动。
    """
    zio = _zotero_io()
    _db, conn = _open()
    try:
        items = zio.fetch_items(limit=limit, start=start)
        return {"fetched": len(items),
                **zio.import_entries(conn, items, dry_run=dry_run)}
    except zio.ZoteroError as exc:
        raise HTTPException(400, str(exc))
    finally:
        conn.close()


@router.get("/export")
def library_export(format: str = "bibtex", collection_id: str | None = None,
                   paper_ids: str | None = None):
    """导出为 BibTeX 或 CSL-JSON（可直接导入 Zotero / EndNote）。"""
    zio = _zotero_io()
    _db, conn = _open()
    try:
        ids = [x.strip() for x in (paper_ids or "").split(",") if x.strip()]
        where, params = ["deleted_at IS NULL"], []
        if ids:
            where.append(f"paper_id IN ({','.join('?' * len(ids))})")
            params.extend(ids)
        elif collection_id:
            where.append("paper_id IN (SELECT paper_id FROM collection_items"
                         " WHERE collection_id=?)")
            params.append(collection_id)
        rows = [dict(r) for r in conn.execute(
            f"SELECT paper_id,title,abstract,journal,year,doi,url,authors_json"
            f" FROM items WHERE {' AND '.join(where)} ORDER BY CAST(paper_id AS INTEGER)",
            params)]
        for r in rows:
            try:
                r["authors"] = json.loads(r.pop("authors_json") or "[]")
            except Exception:
                r["authors"] = []
        if format.lower() in ("csljson", "csl-json", "json"):
            return {"format": "csljson", "count": len(rows),
                    "items": zio.to_csl_json(rows)}
        return {"format": "bibtex", "count": len(rows), "bibtex": zio.to_bibtex(rows)}
    finally:
        conn.close()


# ---------------------------------------------------------------- 文献阅读（R2）

def _reading():
    try:
        from literature import reading
    except ImportError:  # pragma: no cover
        from src.literature import reading  # type: ignore
    return reading


@router.get("/annotations")
def library_annotations(paper_id: str | None = None,
                        attachment_id: str | None = None):
    """批注列表（按页码排序）。"""
    _db, conn = _open()
    try:
        return {"annotations": _reading().list_annotations(
            conn, paper_id=paper_id, attachment_id=attachment_id)}
    finally:
        conn.close()


@router.post("/annotations", status_code=201)
def library_add_annotation(req: LibraryAnnotationCreate):
    """新增批注（高亮/便签/截图；position 为 0~1 归一化坐标）。"""
    _db, conn = _open()
    try:
        return _reading().add_annotation(
            conn, req.paper_id, type_=req.type, page=req.page,
            position=req.position, color=req.color, text=req.text,
            comment=req.comment, attachment_id=req.attachment_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    finally:
        conn.close()


@router.patch("/annotations/{annotation_id}")
def library_update_annotation(annotation_id: str, req: LibraryAnnotationUpdate):
    """改批注（颜色/评论/页码/坐标）。"""
    _db, conn = _open()
    try:
        out = _reading().update_annotation(
            conn, annotation_id, color=req.color, comment=req.comment,
            page=req.page, position=req.position)
        if out is None:
            raise HTTPException(404, f"批注不存在: {annotation_id}")
        return out
    finally:
        conn.close()


@router.delete("/annotations/{annotation_id}")
def library_delete_annotation(annotation_id: str):
    _db, conn = _open()
    try:
        if not _reading().delete_annotation(conn, annotation_id):
            raise HTTPException(404, f"批注不存在: {annotation_id}")
        return {"ok": True, "annotation_id": annotation_id}
    finally:
        conn.close()


@router.get("/reading/queue")
def library_reading_queue(state: str | None = None, limit: int = 100):
    """阅读清单（含阅读状态与批注数、解析状态与缺失项）。"""
    _db, conn = _open()
    try:
        return {"queue": _reading().queue(conn, state=state, limit=limit)}
    finally:
        conn.close()


@router.get("/reading/plan/{paper_id}")
def library_reading_plan(paper_id: str):
    """读完该做什么：未入库→全量解析；已入库→只补缺失项（与列表徽标同口径）。"""
    _db, conn = _open()
    try:
        return _reading().ingest_plan(conn, paper_id)
    finally:
        conn.close()


@router.post("/reading/state")
def library_reading_state(paper_id: str, state: str):
    """设置阅读状态（unread / reading / read / ingested）。"""
    _db, conn = _open()
    try:
        return {"paper_id": paper_id,
                "reading_state": _reading().set_state(conn, paper_id, state)}
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    finally:
        conn.close()


@router.post("/reading/export-note")
def library_reading_export_note(paper_id: str, title: str = ""):
    """把该文献的批注导出为一条笔记（Markdown，含页码与引文）。"""
    _db, conn = _open()
    try:
        note = _reading().export_annotations_as_note(conn, paper_id, title=title)
        if not note:
            raise HTTPException(400, "该文献暂无批注，无法导出笔记")
        return note
    finally:
        conn.close()


# ---------------------------------------------------------------- 翻译 / 问答（R2-d）

def _reading_llm():
    try:
        from literature import reading_llm
    except ImportError:  # pragma: no cover
        from src.literature import reading_llm  # type: ignore
    return reading_llm


@router.post("/reading/translate")
def library_reading_translate(req: ReadingTranslateRequest):
    """段落级学术翻译（DeepSeek；批量 + 段落缓存 + DOI/SMILES/数字单位保护 + 术语校正）。"""
    _db, conn = _open()
    try:
        return _reading_llm().translate_segments(
            conn, req.segments, target_lang=req.target_lang, use_cache=req.use_cache)
    except RuntimeError as exc:                    # 未配置 LLM → 明确指引
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"翻译失败：{type(exc).__name__}: {exc}")
    finally:
        conn.close()


@router.post("/reading/ask")
def library_reading_ask(req: ReadingAskRequest):
    """针对单篇文献提问：上下文 = 摘要 + 选中段落 + 结构化条目 + 图表标注。"""
    _db, conn = _open()
    try:
        out = _reading_llm().ask(conn, req.paper_id, req.question,
                                 selection=req.selection, history=req.history)
        if out.get("error"):
            raise HTTPException(400, out["error"])
        return out
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    finally:
        conn.close()


# ---------------------------------------------------------------- 阅读收件箱（先读后入库）

@router.post("/reading/upload", status_code=201)
async def library_reading_upload(file: UploadFile = File(...), note: str = ""):
    """外部上传 PDF 进阅读清单（**不创建文献条目**，读完再一键入库）。按 sha1 去重。"""
    _db, conn = _open()
    try:
        data = await file.read()
        return _reading().add_inbox_file(conn, file.filename or "paper.pdf", data,
                                         note=note)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    finally:
        conn.close()


@router.get("/reading/inbox")
def library_reading_inbox(state: str | None = None):
    """阅读收件箱（pending=在读数、ingested=已入库、discarded=已丢弃）。"""
    _db, conn = _open()
    try:
        return {"inbox": _reading().list_inbox(conn, state=state)}
    finally:
        conn.close()


@router.get("/reading/inbox/{inbox_id}/file")
def library_reading_inbox_file(inbox_id: str):
    """读取收件箱中的 PDF（供阅读器 iframe 使用）。"""
    _db, conn = _open()
    try:
        rec = _reading().get_inbox(conn, inbox_id)
        if rec is None:
            raise HTTPException(404, f"收件箱条目不存在: {inbox_id}")
        from pathlib import Path
        p = Path(rec["path"])
        if not p.is_file():
            raise HTTPException(404, "文件已丢失")
        return FileResponse(str(p), media_type="application/pdf",
                            filename=rec["filename"])
    finally:
        conn.close()


@router.post("/reading/inbox/{inbox_id}/state")
def library_reading_inbox_state(inbox_id: str, state: str,
                                paper_id: str | None = None):
    """更新收件箱状态；入库时用 paper_id 回填关联的文献编号。"""
    _db, conn = _open()
    try:
        out = _reading().set_inbox_state(conn, inbox_id, state, paper_id=paper_id)
        if out is None:
            raise HTTPException(404, f"收件箱条目不存在: {inbox_id}")
        return out
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    finally:
        conn.close()


@router.delete("/reading/inbox/{inbox_id}")
def library_reading_inbox_delete(inbox_id: str):
    _db, conn = _open()
    try:
        if not _reading().delete_inbox(conn, inbox_id):
            raise HTTPException(404, f"收件箱条目不存在: {inbox_id}")
        return {"ok": True, "inbox_id": inbox_id}
    finally:
        conn.close()


@router.post("/reading/inbox/{inbox_id}/ingest", status_code=201)
def library_reading_inbox_ingest(inbox_id: str, title: str = "",
                                 use_pdf_metadata: bool = True):
    """把收件箱里的 PDF 正式入库（**先读后入库**的收尾动作）。

    链路（与「添加文献」一致，已核对源码）：
      ① 复用 literature 路由的 `extract_pdf` 从 PDF 抽元数据草稿（best-effort）
      ② `resolver.append_paper(entry)` 建条目（DOI 重复 → 409，不重复入库）
      ③ `attachments.save_pdf(..., role='main')` 把 PDF 挂到该条目
      ④ 标收件箱 `ingested` 并回填 `paper_id`
    解析（抽取条目/图片/摘要，耗 token）不在本端点内执行 —— 由前端确认后
    调用既有 `POST /api/literature/{paper_id}/parse`，避免"不点也烧钱"。
    """
    import io

    from starlette.datastructures import UploadFile as StarletteUploadFile

    _db, conn = _open()
    try:
        rec = _reading().get_inbox(conn, inbox_id)
        if rec is None:
            raise HTTPException(404, f"收件箱条目不存在: {inbox_id}")
        from pathlib import Path
        p = Path(rec["path"])
        if not p.is_file():
            raise HTTPException(404, "收件箱文件已丢失")
        data = p.read_bytes()

        # ① 抽元数据（失败不阻塞入库，标题可用参数兜底）
        draft: dict = {}
        if use_pdf_metadata:
            try:
                from . import literature as _lit
                uf = StarletteUploadFile(file=io.BytesIO(data),
                                         filename=rec["filename"])
                got = _lit.extract_pdf(uf) or {}
                draft = got.get("draft") if isinstance(got, dict) else {}
                draft = draft if isinstance(draft, dict) else {}
            except Exception as exc:            # noqa: BLE001
                logger.info("收件箱入库：PDF 元数据抽取失败（继续）：%s", exc)
                draft = {}

        final_title = (title or "").strip() or str(draft.get("title") or "").strip()
        if not final_title:
            raise HTTPException(
                400, "无法从 PDF 识别标题：请在「科研知识库 → 添加文献」补录标题后入库")

        # ② 建条目（DOI 重复则拒绝，避免重复入库）
        try:
            from literature import resolver
        except ImportError:  # pragma: no cover
            from src.literature import resolver  # type: ignore
        doi = resolver.normalize_doi(str(draft.get("doi") or ""))
        if doi:
            hit = resolver.find_by_doi(doi)
            if hit:
                # 已有同 DOI 文献 → 直接把收件箱指向它，不再新建
                _reading().set_inbox_state(conn, inbox_id, "ingested",
                                           paper_id=str(hit[0]))
                return {"paper_id": str(hit[0]), "reused_existing": True,
                        "inbox_id": inbox_id,
                        "message": "该 DOI 已在文献库中，收件箱条目已指向既有文献"}
        from datetime import datetime
        entry = {
            "doi": doi,
            "title": final_title,
            "authors": [str(a).strip() for a in (draft.get("authors") or [])
                        if str(a).strip()],
            "journal": str(draft.get("journal") or "").strip(),
            "year": draft.get("year"),
            "abstract": (str(draft.get("abstract") or "").strip() or None),
            "in_training": False,
            "source": "reading-inbox",
            "added_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        try:
            pid = resolver.append_paper(entry)
        except OSError as exc:
            raise HTTPException(500, f"文献库写入失败：{type(exc).__name__}: {exc}")
        # v1.9.8：同步进镜像，三栏文献库/阅读清单立即可见（best-effort，失败不阻塞）
        _db.mirror_paper(pid, entry)
        try:
            from literature import graph_ingest
            graph_ingest.upsert_paper_node(pid, entry)
        except Exception as exc:  # noqa: BLE001  (# pragma: no cover)
            logger.warning("文献节点入图失败（已入库，可重试）：%s", exc)

        # ③ 挂附件（主文）
        try:
            from literature import attachments as att
        except ImportError:  # pragma: no cover
            from src.literature import attachments as att  # type: ignore
        attach: dict | None = None
        try:
            attach = att.save_pdf(pid, rec["filename"], data, role="main")
        except att.AttachmentError as exc:
            logger.warning("附件保存失败（条目已建）：%s", exc)

        # ④ 收件箱收尾
        _reading().set_inbox_state(conn, inbox_id, "ingested", paper_id=pid)
        conn.commit()
        return {"paper_id": pid, "reused_existing": False, "inbox_id": inbox_id,
                "entry": entry, "attachment": attach,
                "needs_parse": True,
                "next": f"POST /api/literature/{pid}/parse?auto_backfill=true"}
    finally:
        conn.close()
