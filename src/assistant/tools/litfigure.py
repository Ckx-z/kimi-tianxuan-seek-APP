"""助手工具 `view_figure`（v2.0.1 P6）：让助手**真的看图**（视觉模型读文献图）。

## 为什么需要它

此前助手只看得到文献的**文字**（条目/摘要/图注文本），图里的信息（PXRD 峰位、
比表面积、截留率、SEM 形貌…）它一概看不到；用户问「这篇文献的图 3 说了什么」
只能得到含糊回答。本工具把 `src/literature/vision.py` 的视觉读图接到助手侧：

- `mode="list"`：列出某文献的图（fig_id / 页 / 图注摘要 / 标注来源 / 技术），
  让助手先知道"有哪些图"再决定读哪张；
- `mode="read"`：挑一张图 → 送视觉模型 → 返回描述、数值、置信度、读数依据；
- `save_caption=true`：把结果写回该图的标注（`caption_source="vision"`，
  DB 镜像 + 图谱索引两处都写，见 `src/literature/figure_caption.py`）。

## 诚实性（用户在意的红线）

- 视觉未启用 / 调用失败 → 返回 `ok=False` + 中文 `reason` + **可操作建议**
  （去设置页开哪个开关），让助手如实告诉用户「我看不到图」，**绝不假装看过**；
- 返回值里 `caption`/`metrics` 全部来自视觉模型，本工具不编造任何数值；
- `read` 会调用云端视觉模型（**可能产生 API 费用**），`save_caption` 会写数据，
  因此本工具在 registry 里挂了 `confirm`（写操作二次确认）。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

MODES = ("list", "read")
MAX_LIST = 20          # list 最多回 20 条（与工具描述一致）
CAPTION_PREVIEW = 80   # list 里图注截断长度
_SOURCE_LABEL = {"vision": "视觉标注", "pdf": "原文图注",
                 "user": "人工标注", "none": "无图注"}


def _modules():
    try:
        from literature import figure_caption, figures, library_db, vision
    except ImportError:  # pragma: no cover
        from src.literature import figure_caption, figures, library_db, vision  # type: ignore
    return figure_caption, figures, library_db, vision


def _open_conn(library_db):
    library_db.ensure_ready()
    return library_db.connect()


def _base(text: str, *, ok: bool, is_error: bool = False, **fields) -> dict:
    """统一出口形状：registry 契约（text/details/is_error）+ 结构化字段。

    `details` 里同样带 `ok` 与结构化字段（SSE 的 tool_result 卡片只读 details）。
    """
    details = {"ok": bool(ok),
               **{k: v for k, v in fields.items() if k != "details"}}
    return {"text": text, "details": details, "is_error": is_error,
            "ok": bool(ok), **fields}


def _figure_brief(fig: dict, db_row: dict | None = None) -> dict:
    """列表项：fig_id / page / caption(截断) / caption_source / technique。"""
    figure_caption, _figures, _db, _vision = _modules()
    caption = str((db_row or {}).get("caption") or fig.get("caption") or "").strip()
    meta = fig.get("meta") if isinstance(fig.get("meta"), dict) else {}
    technique = str((db_row or {}).get("technique")
                    or meta.get("technique") or "").strip()
    page = figure_caption.figure_page(fig)
    if page is None:
        page = figure_caption.figure_page(db_row)
    return {
        "fig_id": str(fig.get("fig_id") or ""),
        "page": page,
        "caption": caption[:CAPTION_PREVIEW] + ("…" if len(caption) > CAPTION_PREVIEW else ""),
        "caption_source": figure_caption.effective_caption_source(fig, db_row),
        "technique": technique,
    }


def _pick_by_hint(items: list[dict], hint: str) -> tuple[dict | None, str]:
    """按 hint 在 caption/technique 里做关键词匹配；返回 (命中图, 说明)。"""
    keys = [k for k in str(hint or "").replace("，", " ").replace(",", " ").split() if k]
    if not keys:
        return (items[0] if items else None), ""
    for fig in items:
        text = f"{fig.get('caption') or ''} {((fig.get('meta') or {}) if isinstance(fig.get('meta'), dict) else {}).get('technique') or ''}"
        low = text.lower()
        for key in keys:
            if key.lower() in low:
                return fig, f"按关键词「{key}」匹配到该图"
    return None, f"没有图注/技术关键词匹配「{hint}」"


# ---------------------------------------------------------------- list

def _list_mode(paper_id: str) -> dict:
    figure_caption, _figures, library_db, _vision = _modules()
    conn = _open_conn(library_db)
    try:
        if not paper_id:
            # 没给 paper_id：回最近入库的若干张图（并明确提示可补 paper_id 精确查）
            rows = library_db.list_figures(conn, None, limit=MAX_LIST)
            rows.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
            briefs = [_figure_brief(r, r) for r in rows[:MAX_LIST]]
            if not briefs:
                return _base(
                    "文献库里还没有任何文献图片，无法列出。可让用户在文献卡片的"
                    "「补解析」里抽取图片或手动上传后再试。",
                    ok=False, mode="list",
                    reason="未提供 paper_id，且文献库中没有图片",
                    suggestion="请提供 paper_id（文献编号，如 1529）；文献库里目前没有图片")
            lines = [f"- {b['fig_id']}（第 {b['page'] or '?'} 页，"
                     f"{_SOURCE_LABEL.get(b['caption_source'], b['caption_source'])}）："
                     f"{b['caption'] or '（无图注）'}"
                     for b in briefs]
            return _base(
                "未提供 paper_id，以下是**最近入库**的文献图片（最多 20 张）；"
                "如需指定文献请带 paper_id 再调一次：\n" + "\n".join(lines),
                ok=True, mode="list", paper_id="", count=len(briefs), figures=briefs)
        items = figure_caption.merge_paper_figures(conn, paper_id)
        if not items:
            return _base(
                f"文献 {paper_id} 暂时没有图片（尚未抽取或未上传）。",
                ok=False, mode="list", paper_id=paper_id,
                reason=f"文献 {paper_id} 没有文献图",
                suggestion="可在文献卡片用「补解析」抽取文献图，或手动上传图片后再试")
        briefs = [_figure_brief(r, None) for r in items[:MAX_LIST]]
        lines = [f"- {b['fig_id']}（第 {b['page'] or '?'} 页，"
                 f"来源 {_SOURCE_LABEL.get(b['caption_source'], b['caption_source'])}"
                 f"{('，技术 ' + b['technique']) if b['technique'] else ''}）："
                 f"{b['caption'] or '（无图注）'}" for b in briefs]
        more = "" if len(items) <= MAX_LIST else \
            f"\n（共 {len(items)} 张，此处只列前 {MAX_LIST} 张）"
        return _base(
            f"文献 {paper_id} 共有 {len(items)} 张图：\n" + "\n".join(lines) + more,
            ok=True, mode="list", paper_id=paper_id, count=len(items), figures=briefs)
    finally:
        conn.close()


# ---------------------------------------------------------------- read

def _read_mode(paper_id: str, fig_id: str, hint: str, save_caption: bool) -> dict:
    figure_caption, figures, library_db, vision = _modules()
    conn = _open_conn(library_db)
    try:
        target: dict | None = None
        picked_note = ""
        if fig_id:
            target = figures.get_figure(fig_id) or library_db.get_figure(conn, fig_id)
            if target is None:
                return _base(
                    f"找不到图 {fig_id}：可能 fig_id 有误或该图已被删除。",
                    ok=False, mode="read", fig_id=fig_id,
                    reason=f"图不存在：{fig_id}",
                    suggestion="先用 mode=\"list\" 列一下该文献有哪些图（fig_id 形如 fig_1a2b3c4d5e6f）")
        elif paper_id:
            items = figure_caption.merge_paper_figures(conn, paper_id)
            if not items:
                return _base(
                    f"文献 {paper_id} 没有图片可读。",
                    ok=False, mode="read", paper_id=paper_id,
                    reason=f"文献 {paper_id} 没有文献图",
                    suggestion="可在文献卡片用「补解析」抽取文献图，或手动上传图片后再试")
            target, picked_note = _pick_by_hint(items, hint)
            if target is None:
                briefs = [_figure_brief(r) for r in items[:MAX_LIST]]
                return _base(
                    f"{picked_note}。该文献共有 {len(items)} 张图，可用 fig_id 指定要读哪张："
                    + "；".join(f"{b['fig_id']}(第 {b['page'] or '?'} 页)" for b in briefs),
                    ok=False, mode="read", paper_id=paper_id,
                    reason=picked_note or "没有匹配的图",
                    suggestion="改传 fig_id（或用 mode=\"list\" 看图注后重挑）",
                    figures=briefs)
        else:
            return _base(
                "参数不足：请提供 fig_id（直接指定）或 paper_id（配合 hint 挑选）。",
                ok=False, mode="read",
                reason="需要 fig_id 或 paper_id",
                suggestion="先用 mode=\"list\" 看某文献的图，再带 fig_id 调一次")

        if not vision.is_enabled():
            return _base(
                "视觉读图未启用，我无法真正看到这张图（不会凭空描述）。"
                "请到 设置 → 文献解析 LLM → 视觉读图 打开开关并填写支持图片输入的模型。",
                ok=False, mode="read", fig_id=str(target.get("fig_id") or ""),
                reason=f"视觉模型未启用或配置不完整（{figure_caption.VISION_HINT}）",
                suggestion=figure_caption.VISION_SUGGESTION)

        res = figure_caption.caption_one(conn, target, hint=hint, save=save_caption)
        fid = str(target.get("fig_id") or "")
        if not res.get("ok"):
            return _base(
                f"读图失败：{res.get('error')}（如实报告，未编造内容）",
                ok=False, mode="read", fig_id=fid,
                reason=str(res.get("error") or "视觉读图失败"),
                suggestion=figure_caption.VISION_SUGGESTION)
        analysis = res.get("analysis") or {}
        page = figure_caption.figure_page(target)
        metrics = res.get("metrics") or []
        caption = str(res.get("caption") or "")
        saved = bool(res.get("saved"))
        lines = [f"图 {fid}（第 {page or '?'} 页）视觉读图结果：",
                 f"- 描述：{analysis.get('description') or caption or '（模型未给出描述）'}"]
        if res.get("technique"):
            lines.append(f"- 技术：{res['technique']}")
        if metrics:
            lines.append("- 图中数值：" + "；".join(
                f"{m.get('name')} {m.get('value')}"
                f"{(' ' + str(m.get('unit'))) if m.get('unit') else ''}"
                for m in metrics))
        if res.get("confidence"):
            lines.append(f"- 置信度：{res['confidence']}")
        if res.get("notes"):
            lines.append(f"- 读数依据/存疑：{res['notes']}")
        lines.append(f"- 图注：{caption}" if caption else "- 图注：（空）")
        lines.append("已写入视觉标注（caption_source=vision）。" if saved
                     else "本次未写入标注（save_caption=false，仅本次读图使用）。")
        if picked_note:
            lines.insert(1, f"（{picked_note}）")
        return _base(
            "\n".join(lines), ok=True, mode="read", fig_id=fid, page=page,
            technique=str(res.get("technique") or ""),
            description=str(analysis.get("description") or caption),
            metrics=metrics, confidence=str(res.get("confidence") or ""),
            notes=str(res.get("notes") or ""), caption=caption,
            saved=saved,
            message=("已写入视觉标注" if saved else "未写入标注（仅本次读图）"))
    finally:
        conn.close()


# ---------------------------------------------------------------- 入口

def view_figure(args: dict | None = None) -> dict:
    """读文献图（list 列图 / read 视觉读图）。args: {paper_id?, fig_id?, mode?,
    save_caption?, hint?}。"""
    a = dict(args or {})
    mode = str(a.get("mode") or "read").strip().lower()
    paper_id = str(a.get("paper_id") or "").strip()
    fig_id = str(a.get("fig_id") or "").strip()
    hint = str(a.get("hint") or "").strip()
    save_caption = bool(a.get("save_caption"))
    if mode not in MODES:
        return _base(f"不支持的 mode：{mode}；可用 {' / '.join(MODES)}。",
                     ok=False, is_error=True, mode=mode,
                     reason=f"mode 非法：{mode}",
                     suggestion="mode 只能是 list（列图）或 read（读图）")
    try:
        if mode == "list":
            return _list_mode(paper_id)
        return _read_mode(paper_id, fig_id, hint, save_caption)
    except Exception as exc:  # noqa: BLE001 - 工具层绝不抛给上层
        logger.warning("view_figure 执行失败：%s", exc)
        return _base(f"读图工具执行失败：{type(exc).__name__}: {exc}",
                     ok=False, is_error=True, mode=mode,
                     reason=f"{type(exc).__name__}: {exc}",
                     suggestion="可稍后重试；若持续失败请检查文献库是否可用")


def confirm_impact(args: dict | None = None) -> str | None:
    """二次确认文案：只有会**写数据**（save_caption=true 的 read）才需要确认。

    `mode="list"` 与"只读不写"的 read 不弹确认（符合 registry 的
    「confirm 只用于写操作」约定；费用提示写在工具描述里）。
    """
    a = args or {}
    if str(a.get("mode") or "read").strip().lower() != "read":
        return None
    if not a.get("save_caption"):
        return None
    return ("将用视觉模型读图并写入图片标注（可能产生 API 费用），是否继续？")


TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "view_figure",
        "description": "查看文献图片（视觉读图）：list=列出某文献的文献图（图号/页码/"
                       "图注摘要/标注来源/技术），read=用**云端视觉模型**读某张图，"
                       "返回图中描述与可读到的数值（如 PXRD 峰位、比表面积、截留率）。"
                       "用户问「这张图/图 3 说了什么」「文献里的 SEM/PXRD 图是什么样」时用它。"
                       "read 会调用视觉模型**可能产生 API 费用**；save_caption=true 会把"
                       "读图结果写成该图的视觉标注（写数据，需用户二次确认）。"
                       "视觉未启用时本工具会如实返回失败原因，**不会编造图中内容**。",
        "parameters": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": list(MODES),
                         "description": "list=只列图（不花钱）/ read=视觉读图（默认）"},
                "paper_id": {"type": "string",
                             "description": "文献编号（read 时配合 hint 挑图；list 时列出该文献的图）"},
                "fig_id": {"type": "string",
                           "description": "图 id（形如 fig_1a2b3c4d5e6f）；read 时优先按它定位"},
                "save_caption": {"type": "boolean",
                                 "description": "true=把读图结果写入该图的视觉标注"
                                                "（caption_source=vision，需二次确认；默认 false）"},
                "hint": {"type": "string",
                         "description": "读图提示/挑选关键词，如「PXRD 峰位」「截留率」；"
                                        "read 时用于在 caption/technique 里匹配要读的图"},
            },
            "required": [],
        },
    },
}
