"""联网搜索工具（v1.6.0 P0）：provider 抽象（tavily / serper）。

设置页配置（src/llm/client.py 的 web_search 字段）：
- provider=tavily：POST https://api.tavily.com/search（json body 带 api_key）
- provider=serper：POST https://google.serper.dev/search（X-API-KEY 头）

返回统一工具契约 {text, details, is_error}；网络/4xx/5xx 全部转 is_error
中文原因，绝不抛异常（registry.execute 兜底之外自兜一层，保证 LLM 拿到
可读失败信息后能换路）。

红线：api_key 绝不打印、绝不进返回体、绝不落盘（只存 llm_settings.local.json）。
"""

from __future__ import annotations

import requests

try:
    from src.llm import client as llm_client
except ImportError:  # pragma: no cover
    from llm import client as llm_client  # type: ignore

TIMEOUT = 20  # 秒
_MAX_RESULTS = 8
_UA = {"User-Agent": "cof-research-assistant/1.6"}


def _fmt_hits(hits: list[dict]) -> str:
    """命中列表 → LLM 可读中文文本（标题 + URL + 摘要）。"""
    if not hits:
        return "（无搜索结果）"
    lines = []
    for i, h in enumerate(hits, 1):
        title = str(h.get("title") or "").strip() or "（无标题）"
        url = str(h.get("url") or "").strip()
        snippet = str(h.get("snippet") or "").strip().replace("\n", " ")
        if len(snippet) > 260:
            snippet = snippet[:260] + "…"
        lines.append(f"{i}. {title}\n   {url}\n   {snippet}")
    return "\n".join(lines)


def _search_tavily(query: str, n: int, api_key: str) -> list[dict]:
    resp = requests.post(
        "https://api.tavily.com/search",
        json={"api_key": api_key, "query": query,
              "max_results": max(1, min(n, _MAX_RESULTS)),
              "search_depth": "basic"},
        headers=_UA, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    hits = []
    for r in data.get("results") or []:
        hits.append({
            "title": r.get("title") or "",
            "url": r.get("url") or "",
            "snippet": (r.get("content") or r.get("snippet") or ""),
            "source": "tavily",
        })
    return hits


def _search_serper(query: str, n: int, api_key: str) -> list[dict]:
    resp = requests.post(
        "https://google.serper.dev/search",
        json={"q": query, "num": max(1, min(n, _MAX_RESULTS))},
        headers={**_UA, "X-API-KEY": api_key,
                 "Content-Type": "application/json"},
        timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    hits = []
    for r in data.get("organic") or []:
        hits.append({
            "title": r.get("title") or "",
            "url": r.get("link") or "",
            "snippet": r.get("snippet") or "",
            "source": "serper",
        })
    return hits


def web_search(query: str, n: int = 5) -> dict:
    """联网搜索。未配置/失败返回 is_error 结果（不抛异常）。"""
    query = (query or "").strip()
    if not query:
        return {"text": "web_search 参数错误：query 不能为空",
                "details": {}, "is_error": True}
    ok, reason = llm_client.web_search_available()
    if not ok:
        return {"text": f"联网搜索不可用：{reason}",
                "details": {"available": False}, "is_error": True}
    cfg = llm_client.get_search_settings()
    provider = cfg["provider"]
    try:
        if provider == "tavily":
            hits = _search_tavily(query, n, cfg["api_key"])
        else:
            hits = _search_serper(query, n, cfg["api_key"])
    except requests.Timeout:
        return {"text": f"联网搜索超时（>{TIMEOUT}s）：{provider} 无响应，"
                        "请稍后重试或换问题。",
                "details": {"provider": provider}, "is_error": True}
    except requests.HTTPError as exc:
        hint = "API key 无效/额度不足" if exc.response is not None \
            and exc.response.status_code in (401, 402, 403) else "服务端错误"
        return {"text": f"联网搜索失败（{provider}，HTTP "
                        f"{exc.response.status_code if exc.response else '?'}，{hint}）。",
                "details": {"provider": provider}, "is_error": True}
    except requests.RequestException as exc:
        return {"text": f"联网搜索失败（{provider}）：{type(exc).__name__}",
                "details": {"provider": provider}, "is_error": True}
    if not hits:
        return {"text": "联网搜索无结果（可换关键词或改用学术检索）。",
                "details": {"results": []}, "is_error": False}
    return {
        "text": f"联网搜索（{provider}）命中 {len(hits)} 条：\n"
                + _fmt_hits(hits),
        "details": {"results": hits, "provider": provider},
        "is_error": False,
    }
