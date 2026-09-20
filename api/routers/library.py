"""科研文献库 API（v1.9.7 P1/P2，借鉴 Zotero local API 的参数设计）。

这一层是**新的三栏文献库 UI 的后端**：服务端检索（`q/limit/offset/sort/direction/
collection/tag`）+ 集合（树）/标签/笔记 + 跨文献数值检索 + 关系查询。

数据来源：`src/literature/library_db.py` 的 SQLite 单库（镜像既有落盘文件；
原文件仍是写入的事实来源，DB 可随时 `POST /api/library/rebuild` 重建）。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from ..schemas import (LibraryCollectionCreate, LibraryCollectionRename,
                       LibraryNoteCreate, LibraryNoteUpdate, LibraryPaperPatch,
                       LibraryTagSet)

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
