"""文献图片「视觉标注」流水线（v2.0.1 P6）：读图 → 视觉模型 → 写回两处。

## 为什么单独一层

视觉标注的调用方有两个（`api/routers/library.py` 的两个端点、助手工具
`assistant/tools/litfigure.py` 的 `view_figure`），两者必须**同一套语义**：

- 图从哪来：`figures_index.json`（事实来源，`src/literature/figures.py`）为准，
  `library_db.figures`（镜像）补充 `caption_source` 等列；
- 文件怎么取：`file` 是**裸文件名**，落在 `user_data_root()/literature/figures/`
  （见 `figures.resolve_figure_file`）；
- 写哪两处：DB 镜像（`library_db.set_figure_caption`）+ 事实来源索引
  （`figures.update_figure`）—— **只写 DB 会被 `POST /api/library/rebuild` 冲掉**；
- 什么算"已完成"：`caption_source ∈ {vision, user}`（用户人工改过的图不重做，
  除非显式 force）。

## 诚实性

- 未启用视觉 / 模型报错 / 文件缺失 / SVG → 全部返回结构化失败（`ok=False` + 中文
  `error`），**绝不伪造读图结果**，也绝不抛异常打断批量；
- 返给上层/模型的 `caption` 只由视觉模型真的返回的 description + metrics 组成
  （见 `vision.build_caption`）。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: 已有这些来源的图默认不重做（force=True 才重做）
DONE_CAPTION_SOURCES = ("vision", "user")
#: 单次批量的上限（控制视觉 API 费用）；端点/工具传更大值也会被夹到这里
MAX_BATCH = 50
DEFAULT_BATCH = 10
#: 视觉未启用时给用户/助手的可操作提示
VISION_HINT = ("视觉读图未启用：请到「设置 → 文献解析 LLM → 视觉读图」打开开关，并填写"
               "支持图片输入的模型（vision_model）；vision_base_url / vision_api_key "
               "留空时回退主解析 LLM 的端点与 Key。")
VISION_SUGGESTION = "到 设置 → 文献解析 LLM → 视觉读图 开启并填模型"

_MIME_BY_SUFFIX = {".png": "image/png", ".jpg": "image/jpeg",
                   ".jpeg": "image/jpeg", ".webp": "image/webp",
                   ".gif": "image/gif"}


def modules():
    """惰性取模块（沿用本项目「裸名优先」的导入约定，PyInstaller 友好）。"""
    try:
        from literature import figures, library_db, vision
    except ImportError:  # pragma: no cover
        from src.literature import figures, library_db, vision  # type: ignore
    return figures, library_db, vision


# ---------------------------------------------------------------- 只读辅助

def figure_page(rec: dict | None) -> int | None:
    """图的页码：索引记录放 `meta.page`，DB 行还有 `page` 列。"""
    rec = rec or {}
    for value in ((rec.get("meta") or {}).get("page") if isinstance(
            rec.get("meta"), dict) else None, rec.get("page")):
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        try:
            if value is not None and str(value).strip().isdigit():
                return int(str(value).strip())
        except (TypeError, ValueError):
            continue
    return None


def effective_caption_source(rec: dict | None, db_row: dict | None = None) -> str:
    """图的标注来源（pdf|vision|user|none）。

    优先级：vision/user（人工或视觉标注，任何一侧写过都算）> pdf/none > 按图注有无派生。
    —— 这样即便 DB 镜像与索引一时不同步，也不会把用户/视觉标注当成"未标注"重做。
    """
    idx_src = str((rec or {}).get("caption_source") or "").strip().lower()
    db_src = str((db_row or {}).get("caption_source") or "").strip().lower()
    for src in (idx_src, db_src):
        if src in DONE_CAPTION_SOURCES:
            return src
    for src in (idx_src, db_src):
        if src in ("pdf", "none"):
            return src
    return "pdf" if str((rec or {}).get("caption") or "").strip() else "none"


def merge_paper_figures(conn, paper_id: str) -> list[dict]:
    """某文献的图：索引（事实来源）∪ DB 镜像，**按 page 升序**（无页码排最后）。

    合并策略：同一 fig_id 以**索引记录**为主（caption/file/meta 是事实来源），
    DB 行只用来补索引里没有的图（历史数据/索引被清过的情况）、以及补
    `caption_source`/`technique`/`caption`（视觉标注先落 DB 时索引可能还没同步）。
    """
    figs, db, _vision = modules()
    merged: dict[str, dict] = {}
    for rec in figs.list_figures(str(paper_id)):
        fid = str(rec.get("fig_id") or "")
        if fid:
            merged[fid] = rec
    db_rows: dict[str, dict] = {}
    for row in db.list_figures(conn, str(paper_id), limit=2000):
        fid = str(row.get("fig_id") or "")
        if not fid:
            continue
        db_rows[fid] = row
        merged.setdefault(fid, row)
    for fid, rec in merged.items():
        row = db_rows.get(fid)
        # 来源合并："vision/user" 一旦在任一侧写过就算已标注（防止重复读图/覆盖人工）
        rec["caption_source"] = effective_caption_source(rec, row)
        meta = rec.get("meta") if isinstance(rec.get("meta"), dict) else {}
        if not str(meta.get("technique") or "").strip() and row and row.get("technique"):
            rec["meta"] = {**meta, "technique": row["technique"]}
        if row and not str(rec.get("caption") or "").strip() and row.get("caption"):
            rec["caption"] = row["caption"]
    items = list(merged.values())
    items.sort(key=lambda r: (figure_page(r) is None, figure_page(r) or 0,
                              str(r.get("fig_id") or "")))
    return items


def read_figure_bytes(rec: dict) -> tuple[bytes, str, str | None]:
    """图记录 → (图片字节, MIME, 错误)。找不到文件不抛异常，只回中文原因。"""
    figs, _db, _vision = modules()
    path = figs.resolve_figure_file(rec)
    if path is None:
        return b"", "", (f"图片文件不存在或不可读：{rec.get('file') or '（file 字段为空）'}"
                         f"（图谱目录 {figs.FIGURES_DIR}）")
    suffix = path.suffix.lower()
    mime = str(rec.get("mime") or "").strip()
    if suffix == ".svg" or "svg" in mime.lower():
        return b"", "", "SVG 矢量图不支持视觉识别（请上传 PNG/JPG 位图）"
    try:
        data = path.read_bytes()
    except Exception as exc:  # noqa: BLE001 - 读盘失败按结构化错误返回
        return b"", "", f"读取图片失败：{type(exc).__name__}: {exc}"
    if not mime.startswith("image/"):
        mime = _MIME_BY_SUFFIX.get(suffix, "image/png")
    return data, mime, None


# ---------------------------------------------------------------- 标注一张 / 一批

def caption_one(conn, rec: dict, *, hint: str = "", save: bool = True) -> dict:
    """给一张图做视觉标注。

    返回 `{ok, fig_id, caption, analysis, saved, figure, index_synced, error}`。
    `save=False`（助手"只看不写"）时不落任何数据，只回读图结果。
    """
    figs, db, vision = modules()
    fig_id = str(rec.get("fig_id") or "")
    data, mime, err = read_figure_bytes(rec)
    if err:
        return {"ok": False, "fig_id": fig_id, "caption": "", "analysis": None,
                "saved": False, "figure": None, "index_synced": False, "error": err}
    res = vision.caption_figure(data=data, mime=mime, hint=hint)
    if not res.get("ok") or not str(res.get("caption") or "").strip():
        return {"ok": False, "fig_id": fig_id, "caption": "",
                "analysis": res.get("analysis"), "saved": False, "figure": None,
                "index_synced": False,
                "error": str(res.get("error") or "视觉读图失败")}
    caption = str(res["caption"]).strip()
    out = {"ok": True, "fig_id": fig_id, "caption": caption,
           "analysis": res.get("analysis"),
           # 附加信息提到顶层：调用方（API/助手）不必再挖 analysis
           "technique": str(res.get("technique") or "").strip(),
           "metrics": res.get("metrics") or [],
           "confidence": str(res.get("confidence") or "").strip(),
           "notes": str(res.get("notes") or "").strip(),
           "saved": False, "figure": None,
           "index_synced": False, "error": None}
    if not save:
        return out

    # 1) DB 镜像：先确保该图有行（新增图不写 DB，直接 set 会写不进去）
    row = None
    try:
        db.mirror_figure(conn, rec)
        row = db.set_figure_caption(
            conn, fig_id, caption, source="vision",
            technique=res.get("technique"), metrics=res.get("metrics"),
            notes=res.get("notes"), confidence=res.get("confidence"))
    except Exception as exc:  # noqa: BLE001 - DB 写失败不掩盖"模型已读图"的事实
        logger.warning("视觉标注写入 DB 镜像失败（fig=%s）：%s", fig_id, exc)
    out["figure"] = row

    # 2) 事实来源（figures_index.json）：不写这里，rebuild 后视觉标注会丢
    try:
        idx = figs.get_figure(fig_id) or {}
        base_meta = idx.get("meta") if isinstance(idx.get("meta"), dict) else None
        merged = dict(base_meta or rec.get("meta") or {})
        for key in ("technique", "metrics", "notes", "confidence"):
            value = res.get(key)
            if value not in (None, "", [], {}):
                merged[key] = value
        if idx:
            figs.update_figure(fig_id, caption=caption, caption_source="vision",
                               meta=merged)
            out["index_synced"] = True
        # 更新后的"事实来源"记录一并回给调用方（前端徽标/助手都用得上）
        out["figure"] = figs.get_figure(fig_id) or row
    except Exception as exc:  # noqa: BLE001
        logger.warning("视觉标注写回图谱索引失败（DB 已写入，fig=%s）：%s", fig_id, exc)
    out["saved"] = bool(out["index_synced"] or row is not None)
    return out


def caption_paper(conn, paper_id: str, *, fig_ids: list[str] | None = None,
                  limit: int = DEFAULT_BATCH, force: bool = False,
                  hint: str = "") -> dict:
    """按文献批量视觉标注（单张失败不影响其余）。

    - 默认跳过已有 `caption_source ∈ {vision, user}` 的图，`force=True` 才重做；
    - 按 `page` 升序处理；`limit` 只作用于**真正要处理的图**（跳过的不占额度）；
    - 返回 `{ok, paper_id, requested, captioned, skipped, skipped_fig_ids, failed,
      errors, figures, limit, force}`。
    """
    _figs, db, _vision = modules()
    db_rows = {str(r.get("fig_id")): r
               for r in db.list_figures(conn, str(paper_id), limit=2000)}
    items = merge_paper_figures(conn, str(paper_id))
    if fig_ids:
        want = {str(x).strip() for x in fig_ids if str(x).strip()}
        items = [r for r in items if str(r.get("fig_id")) in want]
    requested = len(items)
    skipped: list[str] = []
    todo: list[dict] = []
    for rec in items:
        fid = str(rec.get("fig_id"))
        src = effective_caption_source(rec, db_rows.get(fid))
        if not force and src in DONE_CAPTION_SOURCES:
            skipped.append(fid)
        else:
            todo.append(rec)
    try:
        n_limit = max(1, min(int(limit or DEFAULT_BATCH), MAX_BATCH))
    except (TypeError, ValueError):
        n_limit = DEFAULT_BATCH
    todo = todo[:n_limit]

    figures: list[dict] = []
    errors: list[dict] = []
    for rec in todo:
        res = caption_one(conn, rec, hint=hint, save=True)
        if res.get("ok"):
            figure = res.get("figure") or {"fig_id": res.get("fig_id")}
            figures.append({**figure, "caption": res.get("caption")})
        else:
            errors.append({"fig_id": str(rec.get("fig_id")),
                           "error": str(res.get("error") or "视觉读图失败")})
    return {"ok": True, "paper_id": str(paper_id), "requested": requested,
            "captioned": len(figures), "skipped": len(skipped),
            "skipped_fig_ids": skipped, "failed": len(errors), "errors": errors,
            "figures": figures, "limit": n_limit, "force": bool(force)}
