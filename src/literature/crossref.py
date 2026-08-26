"""Crossref REST API 客户端（免费、无需 key）。

- https://api.crossref.org/works/{doi}            按 DOI 取元数据
- https://api.crossref.org/works?query.bibliographic=...&rows=N   按标题检索
- 礼貌池：所有请求带 mailto 参数与 User-Agent（可用 CROSSREF_MAILTO 环境变量
  覆盖默认占位邮箱）；
- 超时 15s；网络不可用 / 超时 / 上游错误抛 CrossrefError（中文可读信息），
  DOI 不存在抛 CrossrefNotFound —— 调用方据此转 HTTP 错误，不崩溃。
"""

from __future__ import annotations

import logging
import os
import re
from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://api.crossref.org"
TIMEOUT_S = 15
USER_AGENT = "cof-research-assistant/1.0"
# 礼貌池占位邮箱（本机桌面应用无对外邮箱；可用环境变量覆盖）
DEFAULT_MAILTO = "cof-assistant@localhost.localdomain"

_TAG_RE = re.compile(r"<[^>]+>")
_DOI_PREFIX_RE = re.compile(r"^https?://(dx\.)?doi\.org/", re.IGNORECASE)


class CrossrefError(Exception):
    """网络不可用 / 超时 / 上游非 200：message 为用户可读中文。"""


class CrossrefNotFound(CrossrefError):
    """DOI 在 Crossref 不存在（HTTP 404）。"""


def _mailto() -> str:
    return os.environ.get("CROSSREF_MAILTO", "").strip() or DEFAULT_MAILTO


def _get(path: str, params: dict | None = None) -> dict:
    params = dict(params or {})
    params.setdefault("mailto", _mailto())
    try:
        resp = requests.get(
            BASE_URL + path,
            params=params,
            timeout=TIMEOUT_S,
            headers={"User-Agent": f"{USER_AGENT} (mailto:{_mailto()})"},
        )
    except requests.RequestException as exc:
        raise CrossrefError(
            f"无法连接 Crossref（{type(exc).__name__}）：请检查网络后重试"
        ) from exc
    if resp.status_code == 404:
        raise CrossrefNotFound("Crossref 未找到该 DOI 对应的文献")
    if resp.status_code != 200:
        raise CrossrefError(f"Crossref 返回 HTTP {resp.status_code}，请稍后重试")
    try:
        data = resp.json()
    except ValueError as exc:
        raise CrossrefError("Crossref 响应解析失败（非 JSON）") from exc
    if not isinstance(data, dict):
        raise CrossrefError("Crossref 响应格式异常")
    return data


def _clean(text) -> str:
    """去 JATS/HTML 标签并压缩空白。"""
    return re.sub(r"\s+", " ", _TAG_RE.sub("", str(text or ""))).strip()


def _first(value) -> str:
    """Crossref 的 title/container-title 是 list；取首项清洗。"""
    if isinstance(value, list):
        return _clean(value[0]) if value else ""
    return _clean(value)


def work_to_draft(work: dict) -> dict:
    """Crossref work 对象 → 统一「待审核草稿」结构（existing 由路由层补）。"""
    authors: list[str] = []
    for a in work.get("author") or []:
        if not isinstance(a, dict):
            continue
        name = " ".join(
            x for x in (str(a.get("given") or "").strip(),
                        str(a.get("family") or "").strip()) if x
        ).strip()
        if name:
            authors.append(name)
    year = None
    for key in ("published-print", "published-online", "published",
                "issued", "created"):
        parts = ((work.get(key) or {}).get("date-parts") or [[None]])[0]
        if parts and isinstance(parts[0], int):
            year = parts[0]
            break
    doi = _DOI_PREFIX_RE.sub("", str(work.get("DOI") or "").strip())
    abstract = _clean(work.get("abstract"))
    return {
        "title": _first(work.get("title")),
        "authors": authors,
        "journal": _first(work.get("container-title")),
        "year": year,
        "doi": doi,
        "url": f"https://doi.org/{doi}" if doi else None,
        "abstract": abstract or None,
        "source": "crossref",
    }


def lookup_doi(doi: str) -> dict:
    """按 DOI 取元数据草稿；不存在抛 CrossrefNotFound。"""
    doi = _DOI_PREFIX_RE.sub("", str(doi or "").strip())
    if not doi:
        raise CrossrefError("DOI 不能为空")
    data = _get(f"/works/{quote(doi, safe='')}")
    work = data.get("message")
    if not isinstance(work, dict):
        raise CrossrefError("Crossref 响应缺少 work 元数据")
    return work_to_draft(work)


def search_by_title(title: str, rows: int = 3) -> list[dict]:
    """按标题检索，返回前 rows 个候选草稿（可能为空列表）。"""
    title = str(title or "").strip()
    if not title:
        raise CrossrefError("title 不能为空")
    data = _get("/works", params={
        "query.bibliographic": title,
        "rows": max(int(rows), 1),
    })
    items = ((data.get("message") or {}).get("items")) or []
    return [work_to_draft(w) for w in items[:rows] if isinstance(w, dict)]
