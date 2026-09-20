"""Zotero 互操作（v1.9.7 P5）：本地 API 导入 + BibTeX / CSL-JSON 导出。

## 导入（读 Zotero 本地 API）

Zotero 7+ 需在 **Settings → Advanced → “Allow other applications on this computer to
communicate with Zotero”** 勾选，否则一律 `403`（官方文档 web_api/v3/local_api）。
本模块只读、**不碰 zotero.sqlite**（官方明确警告直接改库会坏），端点：
`GET {base}/items?format=json&limit=&start=&itemType=-attachment`（默认 base 为
`http://localhost:23119/api/users/0`，可用环境变量 `ZOTERO_LOCAL_API` 覆盖）。

失败时**显式点名关卡**（借鉴 zotero-skill 的 blocker 命名）：
Zotero 未开 / 本地 API 未启用 / 端口不通 / 无匹配，而不是含糊报错。

## 导出

- `to_bibtex(rows)`：按 `@article` 生成（含 doi/url/journal/year/author）
- `to_csl_json(rows)`：CSL-JSON 数组（Zotero/EndNote 可直接导入）
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

DEFAULT_API = "http://localhost:23119/api/users/0"
IMPORT_COLLECTION_ID = "col_zotero_import"
IMPORT_COLLECTION_NAME = "Zotero 导入"
BATCH_LIMIT = 100
TIMEOUT = 25


class ZoteroError(Exception):
    """带「关卡」说明的导入错误（供 API 原样回给前端）。"""


def api_base() -> str:
    return (os.environ.get("ZOTERO_LOCAL_API") or DEFAULT_API).rstrip("/")


def normalize_doi(doi: str) -> str:
    d = str(doi or "").strip().lower()
    return re.sub(r"^https?://(dx\.)?doi\.org/", "", d)


# ---------------------------------------------------------------- 读本地 API

def fetch_items(limit: int = BATCH_LIMIT, start: int = 0,
                item_type: str = "-attachment", timeout: int = TIMEOUT) -> list[dict]:
    """取 Zotero 条目（默认排除附件/笔记）。失败抛 ZoteroError（含关卡说明）。"""
    qs = urllib.parse.urlencode({"format": "json", "limit": int(limit),
                                 "start": int(start), "itemType": item_type})
    url = f"{api_base()}/items?{qs}"
    req = urllib.request.Request(url, headers={"Accept": "application/json",
                                               "Zotero-API-Version": "3"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:                      # noqa: F821
        if exc.code == 403:
            raise ZoteroError(
                "Zotero 拒绝访问：请在 Zotero 设置 → Advanced 勾选 “Allow other "
                "applications on this computer to communicate with Zotero” 后重试") from exc
        raise ZoteroError(f"Zotero 本地 API 返回 HTTP {exc.code}") from exc
    except Exception as exc:
        raise ZoteroError(
            f"连不上 Zotero 本地 API（{api_base()}）：请确认 Zotero 正在运行、"
            f"本地 API 已启用、端口 23119 未被占用（{type(exc).__name__}）") from exc
    if not isinstance(data, list):
        raise ZoteroError("Zotero 本地 API 返回格式非预期（期望 JSON 数组）")
    return [x for x in data if isinstance(x, dict)]


# ---------------------------------------------------------------- 映射

def _authors_of(data: dict) -> list[str]:
    out: list[str] = []
    for c in (data.get("creators") or []):
        if not isinstance(c, dict):
            continue
        name = str(c.get("name") or "").strip()
        if not name:
            name = " ".join(x for x in (str(c.get("firstName") or "").strip(),
                                        str(c.get("lastName") or "").strip()) if x)
        if name:
            out.append(name)
    return out


def _year_of(data: dict) -> int | None:
    raw = str(data.get("date") or "")
    m = re.search(r"(19|20)\d{2}", raw)
    return int(m.group(0)) if m else None


def to_paper(entry: dict) -> dict | None:
    """Zotero item → 我们的文献条目（字段映射）。无标题则跳过。"""
    data = entry.get("data") if isinstance(entry.get("data"), dict) else entry
    title = str(data.get("title") or "").strip()
    if not title:
        return None
    doi = normalize_doi(data.get("DOI") or "")
    journal = str(data.get("publicationTitle") or data.get("journalAbbreviation")
                  or data.get("bookTitle") or "").strip()
    url = str(data.get("url") or "").strip()
    return {
        "title": title[:300],
        "authors": _authors_of(data),
        "journal": journal[:120],
        "year": _year_of(data),
        "doi": doi,
        "url": url or (f"https://doi.org/{doi}" if doi else ""),
        "abstract": str(data.get("abstractNote") or "").strip()[:4000],
        "source": "zotero-import",
        "extra_json": json.dumps({"zotero_key": entry.get("key"),
                                  "itemType": data.get("itemType")},
                                 ensure_ascii=False),
        "tags": [str(t.get("tag") if isinstance(t, dict) else t).strip()
                 for t in (data.get("tags") or []) if t],
    }


# ---------------------------------------------------------------- 导入

def import_entries(conn, entries: list[dict], *, dry_run: bool = False,
                   folder_name: str = IMPORT_COLLECTION_NAME) -> dict:
    """把 Zotero 条目写入文献库（幂等：DOI 已存在则跳过）。"""
    try:
        from literature import library_db
    except ImportError:  # pragma: no cover
        from src.literature import library_db  # type: ignore
    try:
        from references import titles as titles_mod  # noqa: F401  (保留：文档化依赖关系)
    except ImportError:  # pragma: no cover
        titles_mod = None  # type: ignore
    # 编号规则在 literature.resolver（库内 max(id)+1；内置库到 1781 → 新文献 1782 起）
    try:
        from literature import resolver as _resolver
    except ImportError:  # pragma: no cover
        from src.literature import resolver as _resolver  # type: ignore

    mapped = [p for p in (to_paper(e) for e in entries) if p]
    existing_dois = {normalize_doi(r["doi"] or "") for r in conn.execute(
        "SELECT doi FROM items WHERE deleted_at IS NULL")}
    existing_dois.discard("")
    planned, skipped_dup = [], 0
    for p in mapped:
        if p["doi"] and p["doi"] in existing_dois:
            skipped_dup += 1
            continue
        planned.append(p)
    if dry_run:
        return {"dry_run": True, "parsed": len(mapped), "importable": len(planned),
                "duplicate_doi": skipped_dup, "sample": planned[:3]}

    if not planned:
        return {"imported": 0, "duplicate_doi": skipped_dup, "parsed": len(mapped)}

    col = conn.execute("SELECT collection_id FROM collections WHERE collection_id=?",
                       (IMPORT_COLLECTION_ID,)).fetchone()
    if col is None:
        library_db.create_collection(conn, folder_name)
        conn.execute("UPDATE collections SET collection_id=? WHERE name=?",
                     (IMPORT_COLLECTION_ID, folder_name))
        conn.commit()

    imported = 0
    for p in planned:
        # 编号沿用既有规则（库内 max+1，内置库到 1781 → 新文献 1782 起）
        pid = _resolver.next_paper_id()
        tags = p.pop("tags", [])
        library_db.upsert_paper(conn, pid, p)
        for t in tags:
            try:
                library_db.set_tag(conn, pid, t, add=True, type_="auto")
            except ValueError:
                continue
        library_db.assign_collection(conn, pid, IMPORT_COLLECTION_ID, add=True)
        imported += 1
    return {"imported": imported, "duplicate_doi": skipped_dup,
            "parsed": len(mapped), "collection_id": IMPORT_COLLECTION_ID}


# ---------------------------------------------------------------- 导出

def to_bibtex(rows: list[dict]) -> str:
    """生成 BibTeX（`@article`；key 用 paper_id + 首作者姓氏）。"""
    out: list[str] = []
    for r in rows:
        authors = r.get("authors") or []
        first = (str(authors[0]).split()[-1] if authors else "anon")
        key = f"{r.get('paper_id', 'x')}{re.sub(r'[^A-Za-z]', '', first) or 'anon'}"
        year = r.get("year") or ""
        fields = [
            ("title", r.get("title") or ""),
            ("author", " and ".join(str(a) for a in authors)),
            ("journal", r.get("journal") or ""),
            ("year", str(year)),
            ("doi", r.get("doi") or ""),
            ("url", r.get("url") or ""),
            ("abstract", (r.get("abstract") or "")[:800]),
        ]
        body = ",\n".join(f"  {k} = {{{v}}}" for k, v in fields if v)
        out.append(f"@article{{{key},\n{body}\n}}")
    return "\n\n".join(out) + ("\n" if out else "")


def to_csl_json(rows: list[dict]) -> list[dict]:
    """生成 CSL-JSON（Zotero / EndNote 可直接导入）。"""
    items: list[dict] = []
    for r in rows:
        authors = r.get("authors") or []
        item = {
            "id": str(r.get("paper_id") or ""),
            "type": "article-journal",
            "title": r.get("title") or "",
            "container-title": r.get("journal") or "",
            "DOI": r.get("doi") or "",
            "URL": r.get("url") or "",
            "abstract": r.get("abstract") or "",
        }
        if r.get("year"):
            item["issued"] = {"date-parts": [[int(r["year"])]]}
        if authors:
            item["author"] = []
            for a in authors:
                parts = str(a).split()
                item["author"].append({"family": parts[-1] if parts else "",
                                       "given": " ".join(parts[:-1])})
        items.append({k: v for k, v in item.items() if v not in ("", None, [])})
    return items
