"""学术文献检索工具（v1.6.0 P0）：arXiv / PubMed / Semantic Scholar / Crossref。

全部免费源（Crossref 复用 src/literature/crossref.py），国内可直连、无需 key；
结果统一带 DOI（源数据没有时留空并如实标注），与 v1.5.4 文献 DOI 基建闭环
（回答中的 DOI 渲染为可点击链接、可经引用核验器回溯到本轮检索结果）。

返回统一工具契约 {text, details, is_error}；单源失败降级为空列表并注明，
不因一个源挂了拖垮整个检索（"all" 聚合模式）。
"""

from __future__ import annotations

import re
import urllib.parse
import xml.etree.ElementTree as ET

import requests

try:
    from src.literature import crossref as lit_crossref
except ImportError:  # pragma: no cover
    from literature import crossref as lit_crossref  # type: ignore

TIMEOUT = 20  # 秒
_PER_SOURCE = 5
_UA = {"User-Agent": "cof-research-assistant/1.6 (mailto:cof@example.com)"}

SOURCES = ("arxiv", "pubmed", "semanticscholar", "crossref", "all")


def _fmt_papers(papers: list[dict]) -> str:
    if not papers:
        return "（无命中）"
    lines = []
    for i, p in enumerate(papers, 1):
        authors = ", ".join(p.get("authors") or [])[:120]
        head = p.get("title") or "（无标题）"
        meta = [p.get("journal") or "", str(p.get("year") or "")]
        meta = " · ".join(x for x in meta if x)
        lines.append(
            f"{i}. {head}\n"
            f"   作者: {authors or '（未知）'} | {meta or '（年份未知）'} | "
            f"来源: {p.get('source')}")
        if p.get("doi"):
            lines.append(f"   DOI: {p['doi']}（https://doi.org/{p['doi']}）")
        if p.get("url"):
            lines.append(f"   URL: {p['url']}")
        if p.get("abstract"):
            ab = p["abstract"].replace("\n", " ")
            lines.append(f"   摘要: {ab[:240]}{'…' if len(ab) > 240 else ''}")
    return "\n".join(lines)


# ---------------------------------------------------------------- 各源实现

def _search_arxiv(query: str, n: int) -> list[dict]:
    q = urllib.parse.quote(f'all:"{query}"')
    url = ("https://export.arxiv.org/api/query?"
           f"search_query={q}&start=0&max_results={n}&sortBy=relevance")
    resp = requests.get(url, headers=_UA, timeout=TIMEOUT)
    resp.raise_for_status()
    ns = {"a": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(resp.text)
    out = []
    for entry in root.findall("a:entry", ns):
        title = " ".join((entry.findtext("a:title", "", ns) or "").split())
        authors = [a.findtext("a:name", "", ns) or ""
                   for a in entry.findall("a:author", ns)]
        aid = (entry.findtext("a:id", "", ns) or "").rsplit("/abs/", 1)[-1]
        published = entry.findtext("a:published", "", ns) or ""
        year = published[:4]
        summary = " ".join((entry.findtext("a:summary", "", ns) or "").split())
        out.append({
            "title": title,
            "authors": authors[:6],
            "year": year,
            "journal": "arXiv",
            "doi": "",
            "url": f"https://arxiv.org/abs/{aid}",
            "abstract": summary,
            "source": "arxiv",
        })
    return out


def _search_pubmed(query: str, n: int) -> list[dict]:
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    r1 = requests.get(f"{base}/esearch.fcgi",
                      params={"db": "pubmed", "term": query,
                              "retmax": n, "retmode": "json",
                              "sort": "relevance"},
                      headers=_UA, timeout=TIMEOUT)
    r1.raise_for_status()
    ids = (r1.json().get("esearchresult") or {}).get("idlist") or []
    if not ids:
        return []
    r2 = requests.get(f"{base}/esummary.fcgi",
                      params={"db": "pubmed", "id": ",".join(ids),
                              "retmode": "json"},
                      headers=_UA, timeout=TIMEOUT)
    r2.raise_for_status()
    result = r2.json().get("result") or {}
    out = []
    for pid in ids:
        doc = result.get(pid) or {}
        doi = ""
        for aid in doc.get("articleids") or []:
            if aid.get("idtype") == "doi":
                doi = aid.get("value") or ""
        year = re.search(r"\d{4}", doc.get("pubdate") or "")
        out.append({
            "title": doc.get("title") or "",
            "authors": [a.get("name") for a in doc.get("authors") or []][:6],
            "year": year.group(0) if year else "",
            "journal": doc.get("fulljournalname") or "",
            "doi": doi,
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pid}/",
            "abstract": "",
            "source": "pubmed",
        })
    return out


def _search_semanticscholar(query: str, n: int) -> list[dict]:
    url = "https://api.semanticscholar.org/graph/v1/paper/search"
    resp = requests.get(
        url,
        params={"query": query, "limit": n,
                "fields": "title,authors,year,externalIds,abstract,url"},
        headers=_UA, timeout=TIMEOUT)
    if resp.status_code == 429:
        return []  # 限流：降级为空，不拖垮聚合
    resp.raise_for_status()
    out = []
    for p in (resp.json().get("data") or []):
        out.append({
            "title": p.get("title") or "",
            "authors": [a.get("name") for a in p.get("authors") or []][:6],
            "year": str(p.get("year") or ""),
            "journal": (p.get("venue") or "")[:80],
            "doi": (p.get("externalIds") or {}).get("DOI") or "",
            "url": p.get("url") or "",
            "abstract": (p.get("abstract") or "") or "",
            "source": "semanticscholar",
        })
    return out


def _search_crossref(query: str, n: int) -> list[dict]:
    try:
        drafts = lit_crossref.search_by_title(query, rows=min(n, 5))
    except Exception:
        return []
    out = []
    for d in drafts:
        doi = d.get("doi") or ""
        title = d.get("title") or ""
        # 过滤 SI/补充材料条目：DOI 带 .s00x 后缀或标题含 Supporting 信息
        if re.search(r"\.s\d{3,}$", doi) or re.search(
                r"supporting information|supplementary", title, re.IGNORECASE):
            continue
        out.append({
            "title": title,
            "authors": [str(a) for a in (d.get("authors") or [])][:6],
            "year": str(d.get("year") or ""),
            "journal": d.get("journal") or "",
            "doi": doi,
            "url": d.get("url") or "",
            "abstract": (d.get("abstract") or "") or "",
            "source": "crossref",
        })
    return out


# ---------------------------------------------------------------- 主入口

def _norm_source(source: str) -> str:
    """数据源名归一化：小写去空格 + 常见别名映射（LLM 输出漂移容错）。"""
    s = re.sub(r"[\s_\-]+", "", (source or "").strip().lower())
    aliases = {
        "googlescholar": "semanticscholar",
        "scholar": "semanticscholar",
        "semanticsscholar": "semanticscholar",
        "s2": "semanticscholar",
        "pubmedcentral": "pubmed",
    }
    return aliases.get(s, s)


def academic_search(query: str, source: str = "all", n: int = 5) -> dict:
    """学术文献检索（source ∈ arxiv/pubmed/semanticscholar/crossref/all）。"""
    query = (query or "").strip()
    if not query:
        return {"text": "academic_search 参数错误：query 不能为空",
                "details": {}, "is_error": True}
    source = _norm_source(source or "all")
    if source not in SOURCES:
        return {"text": f"academic_search 参数错误：未知来源 {source!r}"
                        f"（可选 {'/'.join(SOURCES)}）",
                "details": {}, "is_error": True}
    n = max(1, min(int(n or 5), _PER_SOURCE))
    targets = [source] if source != "all" else \
        ["arxiv", "semanticscholar", "crossref", "pubmed"]
    papers: list[dict] = []
    errors: list[str] = []
    for src in targets:
        try:
            if src == "arxiv":
                papers += _search_arxiv(query, n)
            elif src == "pubmed":
                papers += _search_pubmed(query, n)
            elif src == "semanticscholar":
                papers += _search_semanticscholar(query, n)
            else:
                papers += _search_crossref(query, n)
        except Exception as exc:
            errors.append(f"{src}: {type(exc).__name__}")
    # 聚合模式限长：每源最多 n 条，总上限 8
    papers = papers[:8]
    note = f"（{len(targets) - len(errors)}/{len(targets)} 个来源成功"
    if errors:
        note += f"，失败: {'、'.join(errors)}"
    note += "）"
    return {
        "text": f"学术检索（{source}）{note}，命中 {len(papers)} 篇：\n"
                + _fmt_papers(papers),
        "details": {"papers": papers, "source": source,
                    "errors": errors},
        "is_error": False,
    }
