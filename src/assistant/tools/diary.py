"""read_diary 工具：读「科研日记」（只读，不写）。

与 `read_experiment_records` 同风格：handler 返回
{"text": 中文可读摘要, "details": 结构化数据, "is_error": bool}，
无结果时返回**结构化空结果**（details.count=0 / found=false，is_error=False，
与 read_experiment_records 的"未查到"语义一致），参数非法 / 读取异常才
is_error=True。任何情况下都不编造日记内容 —— 没查到就如实说没有。

检索能力（全部可选，可组合）：
- keyword：空格分词，**全部命中**才算匹配；匹配日记标题、正文、
  @ 关联记录 id，以及关联记录自身的实验编号 / 单体名 / 结果；
- date_from / date_to：日记日期（YYYY-MM-DD）闭区间；
- record_id：只看 @ 了该实验记录的日记（rec_YYYYMMDD_NNN）；
- limit：最多返回条数（默认 5，上限 20）。
"""

from __future__ import annotations

_MAX_ENTRIES = 20      # 单次最多返回条数（token 成本控制）
_DEFAULT_LIMIT = 5
_MAX_CONTENT = 1200    # 单篇正文注入上限（超出截断并如实标注原文字数）
_MAX_FIELD = 200
_OUTCOME_ZH = {"film": "成膜", "partial": "部分成膜", "failed": "失败"}


def _store():
    try:
        from src.diary import store as diary_store
    except ImportError:  # pragma: no cover - src 直接在 sys.path 时
        from diary import store as diary_store  # type: ignore
    return diary_store


def _cut(s: str, n: int = _MAX_FIELD) -> str:
    s = str(s or "").strip()
    return s if len(s) <= n else s[:n] + "…"


def _scope_text(kw: str, df: str, dt: str, rid: str) -> str:
    bits = []
    if kw:
        bits.append(f"关键词「{kw}」")
    if df or dt:
        bits.append(f"日期 {df or '不限'}～{dt or '不限'}")
    if rid:
        bits.append(f"关联实验记录 {rid}")
    return "、".join(bits)


def _fmt_mention(info: dict) -> str:
    rid = info.get("record_id") or "?"
    if not info.get("exists"):
        return f"{rid}（⚠ 该实验记录已不存在）"
    bits = [info.get("experiment_no") or "编号未填", info.get("label") or "单体未知"]
    outcome = _OUTCOME_ZH.get(str(info.get("outcome") or ""),
                              str(info.get("outcome") or ""))
    if outcome:
        bits.append(outcome)
    if info.get("date"):
        bits.append(str(info["date"]))
    return f"{rid}（{'，'.join(bits)}）"


def _fmt_entry(entry: dict, index) -> str:
    day = str(entry.get("date") or "日期未知")
    title = str(entry.get("title") or "").strip()
    head = f"### {day} {title}".rstrip()
    lines = [head]
    details = _store().mention_details(entry, index)
    if details:
        lines.append("- 关联实验记录：" + "；".join(_fmt_mention(d) for d in details))
    content = str(entry.get("content") or "")
    truncated = len(content) > _MAX_CONTENT
    lines.append(f"- 正文（原文 {len(content)} 字"
                 + ("，以下为前 %d 字摘录）" % _MAX_CONTENT if truncated else "，全文）")
                 + "：\n" + (content[:_MAX_CONTENT] + "…" if truncated else content))
    return "\n".join(lines)


def read_diary(keyword: str = "", date_from: str = "", date_to: str = "",
               record_id: str = "", limit: int = 0) -> dict:
    """检索科研日记（只读）。按关键词 / 日期范围 / 关联实验记录过滤。"""
    kw = str(keyword or "").strip()
    df = str(date_from or "").strip()
    dt = str(date_to or "").strip()
    rid = str(record_id or "").strip()
    try:
        limit_n = int(limit or 0)
    except (TypeError, ValueError):
        return {"text": f"limit 必须是整数，收到：{limit!r}",
                "details": {}, "is_error": True}
    if limit_n <= 0:
        limit_n = _DEFAULT_LIMIT
    limit_n = min(limit_n, _MAX_ENTRIES)

    try:
        store = _store()
        matched = store.list_entries(keyword=kw, date_from=df, date_to=dt,
                                     record_id=rid)
        total = store.stats().get("total", 0)
        index = store.record_index() if matched else {}
    except ValueError as exc:
        return {"text": f"检索参数有误：{exc}", "details": {}, "is_error": True}
    except Exception as exc:
        return {"text": f"日记读取失败：{type(exc).__name__}: {exc}",
                "details": {}, "is_error": True}

    scope = _scope_text(kw, df, dt, rid)
    base_details = {
        "keyword": kw, "date_from": df, "date_to": dt, "record_id": rid,
        "matched": len(matched), "total": total,
        "entry_ids": [e.get("entry_id") for e in matched[:limit_n]],
        "dates": [e.get("date") for e in matched[:limit_n]],
    }
    if not matched:
        suffix = f"（筛选：{scope}）" if scope else ""
        exists = ("；当前共有 %d 篇日记，但没有一篇符合上述条件" % total
                  if total else "；日记本目前是空的")
        return {
            "text": f"未查到符合条件的日记{suffix}{exists}。"
                    "请如实告知用户日记里没有相关内容，不要编造日记内容。",
            "details": {**base_details, "count": 0, "found": False},
            "is_error": False,
        }

    shown = matched[:limit_n]
    header = (f"共 {len(matched)} 篇日记符合条件"
              + (f"（筛选：{scope}）" if scope else "")
              + f"，按日期倒序显示最近 {len(shown)} 篇：")
    text = header + "\n\n" + "\n\n".join(_fmt_entry(e, index) for e in shown)
    if len(matched) > len(shown):
        text += (f"\n\n（另有 {len(matched) - len(shown)} 篇未显示，"
                 "需要更多可调大 limit 或收窄日期范围）")
    return {
        "text": text,
        "details": {**base_details, "count": len(shown), "found": True},
        "is_error": False,
    }
