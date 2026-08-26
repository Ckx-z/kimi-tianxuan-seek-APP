"""文献解析服务：paper_id → {paper_id, title, doi, url, has_doi}。

单一职责：把文献库（data/paper_titles.json）里的编号引用解析成完整文献
视图，并承担文献库的追加写（录入审核流 / DOI 回填共用）。

- 读：委托 references.titles（唯一缓存源，reload 一处生效）；
- url：doi 存在时 https://doi.org/{doi}，缺失时 None；
- 写：append_paper 纯 dict 追加 + 原子落盘（查询打分链路只读使用，追加
  新 key 不影响既有读取）；审计流水写 data/literature_intake.jsonl。
"""

from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    import runtime_config  # type: ignore
    from references import titles  # type: ignore
except ImportError:  # 包路径导入（项目根在 sys.path 上）
    from src import runtime_config
    from src.references import titles

# 审计流水（可写用户数据目录；frozen 时落 %APPDATA%/COF-Film-Recommend/data）
INTAKE_PATH = runtime_config.user_data_root() / "literature_intake.jsonl"

_DOI_PREFIX_RE = re.compile(r"^https?://(dx\.)?doi\.org/", re.IGNORECASE)

_append_lock = threading.Lock()


# ---------------------------------------------------------------- 基础解析

def normalize_doi(doi) -> str:
    """DOI 规范化：去空白、去 doi.org 前缀；空/非法输入返回 ""。"""
    s = str(doi or "").strip()
    if not s:
        return ""
    return _DOI_PREFIX_RE.sub("", s).strip()


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def resolve_paper(paper_id) -> dict | None:
    """paper_id → {paper_id, title, doi, url, has_doi}；不存在返回 None。"""
    if paper_id is None:
        return None
    pid = str(paper_id).strip()
    if not pid:
        return None
    entry = titles.resolve_entry(pid)
    if entry is None:
        return None
    doi = normalize_doi(entry.get("doi"))
    return {
        "paper_id": pid,
        "title": str(entry.get("title") or "").strip(),
        "doi": doi,
        "url": f"https://doi.org/{doi}" if doi else None,
        "has_doi": bool(doi),
    }


# ---------------------------------------------------------------- 引用 enrichment（内存视图，不落盘）

def _paper_id_of(ref: dict) -> str:
    """从引用 dict 提取 paper_id：显式 paper_id 字段优先；否则 title 为纯数字
    （auto-matched 旧格式把编号存在 title 里）时视为 paper_id。"""
    pid = str(ref.get("paper_id") or "").strip()
    if pid:
        return pid
    t = str(ref.get("title") or "").strip()
    return t if t.isdigit() else ""


def enrich_reference(ref: dict) -> dict:
    """把 auto-matched 编号引用解析为真实标题/DOI/URL，返回新 dict（不改入参）。

    - 非编号引用（user-added 等）原样返回（拷贝）；
    - 编号可解析：占位标题（title == 编号）换成真实标题，补 doi/url，
      保留 paper_id 字段；已有真实标题/DOI 不覆盖；
    - 编号不可解析：仅补 paper_id 字段与 url=None。
    """
    if not isinstance(ref, dict):
        return ref
    pid = _paper_id_of(ref)
    if not pid:
        return dict(ref)
    out = dict(ref)
    out["paper_id"] = pid
    paper = resolve_paper(pid)
    if paper is None:
        out.setdefault("url", None)
        return out
    if str(out.get("title") or "").strip() == pid:
        out["title"] = paper["title"] or pid
    if not normalize_doi(out.get("doi")):
        out["doi"] = paper["doi"]
    out["url"] = paper["url"]
    return out


def enrich_references(refs) -> list:
    """批量 enrichment；非 list 入参原样返回。"""
    if not isinstance(refs, list):
        return refs
    return [enrich_reference(r) for r in refs]


# ---------------------------------------------------------------- 文献库写入（录入审核流 / 回填共用）

def _papers_path() -> Path:
    """文献库路径（跟随 titles.TITLES_PATH，测试 monkeypatch 一处即生效）。"""
    return Path(titles.TITLES_PATH)


def next_paper_id() -> str:
    """新 paper_id = 现有最大数字 id + 1；空库从 "1" 开始。"""
    ids = [int(k) for k in titles._load() if str(k).isdigit()]
    return str(max(ids) + 1) if ids else "1"


def find_by_doi(doi) -> tuple[str, dict] | None:
    """按 DOI（大小写/前缀不敏感）查文献库，返回 (paper_id, entry)；未命中 None。"""
    target = normalize_doi(doi).lower()
    if not target:
        return None
    for pid, entry in titles._load().items():
        if not isinstance(entry, dict):
            continue
        if normalize_doi(entry.get("doi")).lower() == target:
            return str(pid), dict(entry)
    return None


def append_paper(entry: dict) -> str:
    """追加新文献到 paper_titles.json，返回新 paper_id。

    纯 dict 追加（既有 key 原样保留）+ 临时文件原子替换；写后清 titles
    缓存，下次读取即含新条目。线程安全（模块级锁）。
    """
    with _append_lock:
        papers = dict(titles._load())
        pid = next_paper_id()
        papers[pid] = entry
        path = _papers_path()
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(
            json.dumps(papers, ensure_ascii=False, indent=1) + "\n",
            encoding="utf-8",
        )
        tmp.replace(path)
        titles.reload()
        return pid


def append_intake(record: dict) -> None:
    """追加一行审计流水到 data/literature_intake.jsonl。"""
    record = dict(record)
    record.setdefault("at", _now_iso())
    INTAKE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with INTAKE_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
