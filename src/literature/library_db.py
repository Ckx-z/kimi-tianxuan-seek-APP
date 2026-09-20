"""科研文献库 SQLite 单库（v1.9.7 P1，借鉴 Zotero 数据模型）。

## 设计定位（**重要：本批为「镜像 + 组织层」，不是替换**）

现有落盘文件仍是**写入的事实来源（source of record）**，本模块把数据**镜像**进
`user_data_root()/literature/library.db`，用于：

1. **服务端检索**：`q/limit/offset/sort/direction/collection/tag` —— 替代「`/papers` 全量返回
   1711 条 + 前端本地过滤」；
2. **组织维度**：集合（树，默认 5 个 + 自定义）、标签（手动/自动）、笔记（Markdown）；
3. **为后续批次铺路**：`relations` / `graph_nodes` / `graph_edges` / `entry_metrics`
   供 P3 图谱扩展与跨文献数值检索使用。

这样做的理由：一次性把写入切到 DB 风险大（1711 文献 + 11293 条目 + 55 图 + 5 附件），
而「镜像 + 组织层」可以**随时丢弃 DB 重建**（`rebuild()`），不影响任何既有功能。

## 借鉴要点（对应 docs/Zotero借鉴-文献库与知识图谱重构方案.md）

- `items` 统一主表 + `key`（稳定外部标识）+ `version`（乐观并发）+ `deleted_at`（软删除）；
- 子资源表：`attachments` / `figures` / `entries` / `notes`，都用 `item_id` 外键指向 `items`；
- 集合树（`parent_id` 自引用）+ 多对多（`collection_items`）+ 手排序（`order_index`）；
- 标签字典 + 多对多（`item_tags`，`type` 区分 manual/auto）；**标签颜色放 `settings`**；
- `relations(subject, predicate, object)` 承载开放关系（引用/相似/衍生……）；
- 全文检索用 **FTS5**（`items_fts`）；
- 软删除：`deleted_at` 列 + 查询默认过滤。
"""

from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import time
import uuid
from datetime import datetime
from pathlib import Path

try:
    from src import runtime_config
except ImportError:  # pragma: no cover
    import runtime_config  # type: ignore

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

# 默认集合（用户已确认：先给默认集合 + 自定义）
DEFAULT_COLLECTIONS: list[tuple[str, str]] = [
    ("col_cof_film", "COF 成膜"),
    ("col_char", "表征方法"),
    ("col_dft", "计算"),
    ("col_review", "综述"),
    ("col_toread", "待读"),
]

ALLOWED_ITEM_FIELDS = ("title", "abstract", "journal", "year", "doi", "url",
                       "authors_json", "source", "extra_json", "item_type")


def db_path() -> Path:
    """文献库 DB 路径（用户可写目录，**不再写安装目录**）。"""
    return runtime_config.user_data_root() / "literature" / "library.db"


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def new_item_key() -> str:
    """8 字符库内唯一标识（借鉴 Zotero key；用大写字母+数字，去掉易混字符）。"""
    alphabet = "23456789ABCDEFGHIJKLMNPQRSTUVWXYZ"
    return "".join(alphabet[uuid.uuid4().int % len(alphabet)] for _ in range(8))


# ---------------------------------------------------------------- 连接与建表

_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  id INTEGER PRIMARY KEY,
  key TEXT UNIQUE,
  paper_id TEXT UNIQUE NOT NULL,
  item_type TEXT DEFAULT 'journalArticle',
  title TEXT, abstract TEXT, journal TEXT, year INTEGER, doi TEXT, url TEXT,
  authors_json TEXT,
  source TEXT,
  extra_json TEXT,
  added_at TEXT, updated_at TEXT,
  deleted_at TEXT,
  version INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_items_year ON items(year);
CREATE INDEX IF NOT EXISTS idx_items_source ON items(source);
CREATE INDEX IF NOT EXISTS idx_items_doi ON items(doi);

CREATE TABLE IF NOT EXISTS attachments (
  id INTEGER PRIMARY KEY,
  file_id TEXT UNIQUE, item_id INTEGER REFERENCES items(id) ON DELETE CASCADE,
  paper_id TEXT,                     -- 冗余保存：即使 items 里没有该文献也不丢数据
  role TEXT DEFAULT 'main', filename TEXT, path TEXT, sha1 TEXT UNIQUE,
  size INTEGER, pages INTEGER, chars INTEGER, mime TEXT,
  link_mode TEXT DEFAULT 'imported_file',
  caption TEXT, page INTEGER, created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_att_item ON attachments(item_id);
CREATE INDEX IF NOT EXISTS idx_att_paper ON attachments(paper_id);

CREATE TABLE IF NOT EXISTS figures (
  id INTEGER PRIMARY KEY,
  fig_id TEXT UNIQUE, item_id INTEGER REFERENCES items(id) ON DELETE CASCADE,
  paper_id TEXT,
  attachment_id INTEGER,
  figure_type TEXT, caption TEXT,
  caption_source TEXT,              -- pdf | vision | user | none
  tags_json TEXT, meta_json TEXT, file TEXT, mime TEXT, size INTEGER,
  page INTEGER, score_note TEXT, created_at TEXT, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_fig_item ON figures(item_id);
CREATE INDEX IF NOT EXISTS idx_fig_paper ON figures(paper_id);
CREATE INDEX IF NOT EXISTS idx_fig_type ON figures(figure_type);

CREATE TABLE IF NOT EXISTS entries (
  id INTEGER PRIMARY KEY,
  entry_id TEXT UNIQUE, item_id INTEGER REFERENCES items(id) ON DELETE CASCADE,
  paper_id TEXT,
  group_id TEXT, kind TEXT, experiment TEXT, evidence TEXT,
  source TEXT, status TEXT, graph_indexed INTEGER DEFAULT 0,
  payload_json TEXT, created_at TEXT, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_entry_item ON entries(item_id);
CREATE INDEX IF NOT EXISTS idx_entry_paper ON entries(paper_id);
CREATE INDEX IF NOT EXISTS idx_entry_kind ON entries(kind);

CREATE TABLE IF NOT EXISTS entry_metrics (
  id INTEGER PRIMARY KEY,
  entry_id TEXT, item_id INTEGER,
  name TEXT, value REAL, unit TEXT, technique TEXT, sample TEXT
);
CREATE INDEX IF NOT EXISTS idx_metric_name ON entry_metrics(name);
CREATE INDEX IF NOT EXISTS idx_metric_entry ON entry_metrics(entry_id);

CREATE TABLE IF NOT EXISTS notes (
  id INTEGER PRIMARY KEY,
  note_id TEXT UNIQUE, item_id INTEGER REFERENCES items(id) ON DELETE CASCADE,
  parent_note_id TEXT,
  title TEXT, content_md TEXT,
  source TEXT DEFAULT 'user',       -- user | assistant | llm
  created_at TEXT, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_note_item ON notes(item_id);

CREATE TABLE IF NOT EXISTS collections (
  id INTEGER PRIMARY KEY,
  collection_id TEXT UNIQUE, name TEXT NOT NULL,
  parent_id TEXT, order_index INTEGER DEFAULT 0,
  created_at TEXT, deleted_at TEXT
);
CREATE TABLE IF NOT EXISTS collection_items (
  collection_id TEXT, paper_id TEXT,
  order_index INTEGER DEFAULT 0, added_at TEXT,
  PRIMARY KEY (collection_id, paper_id)
);

CREATE TABLE IF NOT EXISTS tags (
  id INTEGER PRIMARY KEY, name TEXT UNIQUE,
  type TEXT DEFAULT 'manual'        -- manual | auto
);
CREATE TABLE IF NOT EXISTS item_tags (
  paper_id TEXT, tag TEXT, type TEXT DEFAULT 'manual', added_at TEXT,
  PRIMARY KEY (paper_id, tag)
);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY, value_json TEXT, updated_at TEXT
);

CREATE TABLE IF NOT EXISTS relations (
  id INTEGER PRIMARY KEY,
  subject TEXT, predicate TEXT, object TEXT,
  source TEXT, confidence REAL, meta_json TEXT, created_at TEXT,
  UNIQUE(subject, predicate, object)
);
CREATE INDEX IF NOT EXISTS idx_rel_subject ON relations(subject);
CREATE INDEX IF NOT EXISTS idx_rel_object ON relations(object);

CREATE TABLE IF NOT EXISTS graph_nodes (
  node_id TEXT PRIMARY KEY, node_type TEXT, name TEXT, meta_json TEXT
);
CREATE TABLE IF NOT EXISTS graph_edges (
  subject TEXT, predicate TEXT, object TEXT,
  source TEXT, confidence REAL, meta_json TEXT,
  PRIMARY KEY (subject, predicate, object)
);

CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
  paper_id UNINDEXED, title, abstract, authors, tags
);
"""

_COLLECTION_ITEM_UNUSED_NOTE = "collection_items 用 paper_id 关联（对外保持 paper_id 语义不变）"


def connect(path: Path | None = None) -> sqlite3.Connection:
    """打开连接（WAL + 外键约束 + Row 工厂）。调用方负责 close。"""
    target = Path(path) if path else db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def is_initialized(path: Path | None = None) -> bool:
    target = Path(path) if path else db_path()
    if not target.is_file():
        return False
    try:
        with connect(target) as conn:
            row = conn.execute(
                "SELECT value_json FROM settings WHERE key='migrated_at'").fetchone()
        return row is not None
    except Exception:
        return False


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    for cid, name in DEFAULT_COLLECTIONS:
        conn.execute(
            "INSERT OR IGNORE INTO collections(collection_id,name,parent_id,"
            "order_index,created_at) VALUES(?,?,NULL,?,?)",
            (cid, name, DEFAULT_COLLECTIONS.index((cid, name)), _now()))
    conn.execute(
        "INSERT OR REPLACE INTO settings(key,value_json,updated_at) VALUES(?,?,?)",
        ("schema_version", json.dumps(SCHEMA_VERSION), _now()))
    conn.commit()


# ---------------------------------------------------------------- 迁移

def _read_json(path: Path) -> dict:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _figures_from_file(path: Path) -> list[dict]:
    """读图谱索引：**兼容 list 与 {"figures": [...]} 两种形态**。

    （实测踩坑：真实 `figures_index.json` 顶层是 list，早期只用 `_read_json`
    会得到空 dict → 迁移后图谱 55 张全丢。）
    """
    if not path.is_file():
        return []
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(obj, list):
        return [r for r in obj if isinstance(r, dict)]
    if isinstance(obj, dict):
        for key in ("figures", "items", "records"):
            val = obj.get(key)
            if isinstance(val, list):
                return [r for r in val if isinstance(r, dict)]
    return []


def _read_jsonl(path: Path) -> list[dict]:
    out: list[dict] = []
    if not path.is_file():
        return out
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if isinstance(obj, dict):
                out.append(obj)
    except Exception as exc:  # pragma: no cover
        logger.warning("读取 %s 失败：%s", path, exc)
    return out


def _library_sources() -> list[Path]:
    """文献库来源（按优先级：用户覆盖 > 内置随包）。"""
    paths: list[Path] = []
    try:
        user = runtime_config.user_data_root() / "literature" / "paper_titles.json"
        if user.is_file():
            paths.append(user)
        builtin = runtime_config.resource_root() / "data" / "paper_titles.json"
        if builtin.is_file():
            paths.append(builtin)
    except Exception:  # pragma: no cover
        pass
    return paths


def backup_sources(backup_dir: Path | None = None) -> Path:
    """迁移前把原文件**只读备份**到 `literature/_backup_files_<ts>/`（用户已确认）。"""
    root = runtime_config.user_data_root() / "literature"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = Path(backup_dir) if backup_dir else (root / f"_backup_files_{stamp}")
    target.mkdir(parents=True, exist_ok=True)
    for p in _library_sources():
        try:
            shutil.copy2(p, target / p.name)
        except Exception as exc:  # pragma: no cover
            logger.warning("备份 %s 失败：%s", p, exc)
    for name in ("pdfs_index.jsonl", "figures_index.json", "knowledge_entries.jsonl"):
        src = root / name
        if src.is_file():
            try:
                shutil.copy2(src, target / name)
            except Exception as exc:  # pragma: no cover
                logger.warning("备份 %s 失败：%s", src, exc)
    return target


def migrate_from_files(conn: sqlite3.Connection, *, backup: bool = True) -> dict:
    """把 4 个既有文件迁移进 DB（幂等：已迁移则跳过，除非 force）。

    返回 {"papers": n, "attachments": n, "figures": n, "entries": n,
          "metrics": n, "backup": path|None, "skipped": bool}
    """
    row = conn.execute(
        "SELECT value_json FROM settings WHERE key='migrated_at'").fetchone()
    if row is not None:
        return {"skipped": True, "reason": "already-migrated"}

    backup_path = backup_sources() if backup else None
    root = runtime_config.user_data_root() / "literature"

    # 1) 文献库：内置 → 用户覆盖（后写覆盖前者）
    papers: dict[str, dict] = {}
    for src in reversed(_library_sources()):
        for pid, entry in _read_json(src).items():
            if isinstance(entry, dict):
                papers[str(pid)] = entry
    n_papers = 0
    for pid, entry in papers.items():
        upsert_paper(conn, pid, entry, commit=False)
        n_papers += 1

    # 2) 附件（paper_id 冗余保存：即使 items 无该文献也不丢）
    n_att = 0
    for rec in _read_jsonl(root / "pdfs_index.jsonl"):
        pid = str(rec.get("paper_id") or "")
        conn.execute(
            "INSERT OR IGNORE INTO attachments(file_id,item_id,paper_id,role,filename,"
            "path,sha1,size,pages,chars,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (rec.get("file_id"), _item_id(conn, pid), pid, rec.get("role") or "main",
             rec.get("filename"), str(rec.get("path") or ""), rec.get("sha1"),
             rec.get("size"), rec.get("pages"), rec.get("chars"),
             rec.get("uploaded_at")))
        n_att += 1

    # 3) 文献图片（兼容 list / dict 两种索引形态）
    n_fig = 0
    for rec in _figures_from_file(root / "figures_index.json"):
        pid = str(rec.get("paper_id") or "")
        meta = rec.get("meta") or {}
        conn.execute(
            "INSERT OR IGNORE INTO figures(fig_id,item_id,paper_id,figure_type,caption,"
            "caption_source,tags_json,meta_json,file,mime,size,page,score_note,"
            "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rec.get("fig_id"), _item_id(conn, pid), pid, rec.get("figure_type"),
             rec.get("caption"),
             ("pdf" if rec.get("caption") else "none"),
             json.dumps(rec.get("tags") or [], ensure_ascii=False),
             json.dumps(meta, ensure_ascii=False, default=str),
             rec.get("file"), rec.get("mime"), rec.get("size"),
             meta.get("page") if isinstance(meta.get("page"), int) else None,
             rec.get("score_note"), rec.get("created_at"), rec.get("updated_at")))
        n_fig += 1

    # 4) 结构化条目 + 数值指标（item_id 可为空：条目可能属于随包图谱里、
    #    但不在这 1711 篇用户文献库中的 paper_id —— 实测有 ~8500 条属此类，
    #    必须保留而不是丢弃）
    n_ent = n_met = n_orphan = 0
    for rec in _read_jsonl(root / "knowledge_entries.jsonl"):
        pid = str(rec.get("paper_id") or "")
        item_id = _item_id(conn, pid) if pid else None
        if item_id is None:
            n_orphan += 1
        entry_id = str(rec.get("entry_id") or f"ent_{uuid.uuid4().hex[:12]}")
        conn.execute(
            "INSERT OR IGNORE INTO entries(entry_id,item_id,paper_id,group_id,kind,"
            "experiment,evidence,source,status,graph_indexed,payload_json,"
            "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (entry_id, item_id, pid, rec.get("group_id"), rec.get("kind"),
             rec.get("experiment"), rec.get("evidence"), rec.get("source"),
             rec.get("status"), 1 if rec.get("graph_indexed") else 0,
             json.dumps(rec, ensure_ascii=False, default=str),
             rec.get("created_at"), rec.get("updated_at")))
        n_ent += 1
        for m in (rec.get("metrics") or []):
            if not isinstance(m, dict):
                continue
            try:
                value = float(m.get("value"))
            except (TypeError, ValueError):
                continue
            conn.execute(
                "INSERT INTO entry_metrics(entry_id,item_id,name,value,unit,"
                "technique,sample) VALUES(?,?,?,?,?,?,?)",
                (entry_id, item_id, str(m.get("name") or ""), value,
                 str(m.get("unit") or ""), rec.get("technique"),
                 rec.get("sample")))
            n_met += 1

    conn.execute(
        "INSERT OR REPLACE INTO settings(key,value_json,updated_at) VALUES(?,?,?)",
        ("migrated_at", json.dumps(_now()), _now()))
    conn.execute(
        "INSERT OR REPLACE INTO settings(key,value_json,updated_at) VALUES(?,?,?)",
        ("migrated_counts",
         json.dumps({"papers": n_papers, "attachments": n_att,
                     "figures": n_fig, "entries": n_ent, "metrics": n_met,
                     "entries_without_item": n_orphan},
                    ensure_ascii=False), _now()))
    conn.commit()
    logger.info("文献库迁移完成：papers=%s att=%s fig=%s ent=%s met=%s orphan=%s",
                n_papers, n_att, n_fig, n_ent, n_met, n_orphan)
    return {"papers": n_papers, "attachments": n_att, "figures": n_fig,
            "entries": n_ent, "metrics": n_met, "entries_without_item": n_orphan,
            "backup": str(backup_path) if backup_path else None, "skipped": False}


def verify_migration(conn: sqlite3.Connection) -> dict:
    """迁移校验：DB 计数 vs 源文件计数（逐类比对 + 抽取抽样 DOI 比对）。"""
    root = runtime_config.user_data_root() / "literature"
    papers_file: dict[str, dict] = {}
    for src in reversed(_library_sources()):
        for pid, entry in _read_json(src).items():
            if isinstance(entry, dict):
                papers_file[str(pid)] = entry
    figs_file = root / "figures_index.json"
    fig_list = _figures_from_file(figs_file)
    expected = {
        "papers": len(papers_file),
        "attachments": len(_read_jsonl(root / "pdfs_index.jsonl")),
        "figures": len(fig_list),
        "entries": len(_read_jsonl(root / "knowledge_entries.jsonl")),
    }
    actual = {
        "papers": conn.execute(
            "SELECT COUNT(*) c FROM items WHERE deleted_at IS NULL").fetchone()["c"],
        "attachments": conn.execute("SELECT COUNT(*) c FROM attachments").fetchone()["c"],
        "figures": conn.execute("SELECT COUNT(*) c FROM figures").fetchone()["c"],
        "entries": conn.execute("SELECT COUNT(*) c FROM entries").fetchone()["c"],
    }
    mismatch = {k: {"expected": expected[k], "actual": actual[k]}
                for k in expected if expected[k] != actual[k]}
    # 抽样：DOI 一致性（最多 20 条）
    doi_checked = doi_bad = 0
    for pid, entry in list(papers_file.items())[:20]:
        doi_checked += 1
        row = conn.execute("SELECT doi FROM items WHERE paper_id=?", (pid,)).fetchone()
        if row is None or (row["doi"] or "") != str(entry.get("doi") or ""):
            doi_bad += 1
    return {"expected": expected, "actual": actual, "mismatch": mismatch,
            "doi_checked": doi_checked, "doi_mismatch": doi_bad,
            "ok": not mismatch and doi_bad == 0}


def ensure_ready(*, auto_migrate: bool = True) -> dict:
    """确保 DB 存在并已迁移（进程内只做一次）。返回状态摘要。

    注意：`with sqlite3.connect(...)` **不会关闭连接**（只提交/回滚事务），
    这里必须显式 close——否则 Windows 下句柄泄漏会锁住 DB 文件，导致 `rebuild()` 失败。
    """
    global _READY
    if _READY is not None:
        return _READY
    target = db_path()
    fresh = not target.is_file()
    conn = connect(target)
    try:
        init_schema(conn)
        status: dict = {"db": str(target), "fresh": fresh}
        if auto_migrate:
            status["migration"] = migrate_from_files(conn)
        status["verify"] = verify_migration(conn)
    finally:
        conn.close()
    _READY = status
    return status


_READY: dict | None = None


def reset_ready_cache() -> None:
    """测试用：清除进程内「已就绪」缓存。"""
    global _READY
    _READY = None


# ---------------------------------------------------------------- 条目读写

def _item_id(conn: sqlite3.Connection, paper_id: str) -> int | None:
    row = conn.execute("SELECT id FROM items WHERE paper_id=? AND deleted_at IS NULL",
                       (str(paper_id),)).fetchone()
    return int(row["id"]) if row else None


def _fts_refresh(conn: sqlite3.Connection, paper_id: str) -> None:
    """重建该条目的 FTS 行（title/abstract/authors/tags）。"""
    row = conn.execute(
        "SELECT i.id, i.paper_id, i.title, i.abstract, i.authors_json,"
        " (SELECT group_concat(tag, ' ') FROM item_tags WHERE paper_id=i.paper_id) tags"
        " FROM items i WHERE i.paper_id=?", (str(paper_id),)).fetchone()
    if row is None:
        return
    conn.execute("DELETE FROM items_fts WHERE paper_id=?", (str(paper_id),))
    authors = ""
    try:
        arr = json.loads(row["authors_json"] or "[]")
        if isinstance(arr, list):
            authors = " ".join(
                (a.get("name") if isinstance(a, dict) else str(a)) for a in arr)
    except Exception:
        authors = str(row["authors_json"] or "")
    conn.execute(
        "INSERT INTO items_fts(paper_id,title,abstract,authors,tags) VALUES(?,?,?,?,?)",
        (str(paper_id), row["title"] or "", row["abstract"] or "", authors,
         row["tags"] or ""))


def upsert_paper(conn: sqlite3.Connection, paper_id: str, entry: dict,
                 *, commit: bool = True) -> dict:
    """插入或更新文献（元数据）。返回 DB 行。"""
    pid = str(paper_id)
    authors = entry.get("authors")
    authors_json = (json.dumps(authors, ensure_ascii=False)
                    if isinstance(authors, list)
                    else (entry.get("authors_json") or "[]"))
    row = conn.execute("SELECT id, key, version FROM items WHERE paper_id=?",
                       (pid,)).fetchone()
    now = _now()
    if row is None:
        cur = conn.execute(
            "INSERT INTO items(key,paper_id,item_type,title,abstract,journal,year,"
            "doi,url,authors_json,source,extra_json,added_at,updated_at,version)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
            (new_item_key(), pid, entry.get("item_type") or "journalArticle",
             entry.get("title") or "", entry.get("abstract") or "",
             entry.get("journal") or "", entry.get("year"), entry.get("doi") or "",
             entry.get("url") or "", authors_json, entry.get("source") or "builtin",
             entry.get("extra_json") or "{}",
             entry.get("added_at") or now, now))
        item_id = int(cur.lastrowid or 0)
    else:
        item_id = int(row["id"])
        conn.execute(
            "UPDATE items SET title=COALESCE(NULLIF(?,''),title),"
            " abstract=COALESCE(NULLIF(?,''),abstract),"
            " journal=COALESCE(NULLIF(?,''),journal),"
            " year=COALESCE(?,year), doi=COALESCE(NULLIF(?,''),doi),"
            " url=COALESCE(NULLIF(?,''),url), authors_json=?,"
            " source=COALESCE(NULLIF(?,''),source), extra_json=?," 
            " updated_at=?, version=version+1 WHERE id=?",
            (entry.get("title") or "", entry.get("abstract") or "",
             entry.get("journal") or "", entry.get("year"),
             entry.get("doi") or "", entry.get("url") or "", authors_json,
             entry.get("source") or "", entry.get("extra_json") or "{}",
             now, item_id))
    _fts_refresh(conn, pid)
    if commit:
        conn.commit()
    return {"id": item_id, "paper_id": pid}


def get_paper(conn: sqlite3.Connection, paper_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM items WHERE paper_id=? AND deleted_at IS NULL",
        (str(paper_id),)).fetchone()
    if row is None:
        return None
    out = dict(row)
    try:
        out["authors"] = json.loads(out.pop("authors_json") or "[]")
    except Exception:
        out["authors"] = []
    out["collections"] = [r["collection_id"] for r in conn.execute(
        "SELECT collection_id FROM collection_items WHERE paper_id=?", (str(paper_id),))]
    out["tags"] = [r["tag"] for r in conn.execute(
        "SELECT tag FROM item_tags WHERE paper_id=?", (str(paper_id),))]
    return out


_SORT_COLUMNS = {"year": "i.year", "added_at": "i.added_at", "title": "i.title",
                 "updated_at": "i.updated_at", "paper_id": "CAST(i.paper_id AS INTEGER)"}


def search_papers(conn: sqlite3.Connection, *, q: str = "", limit: int = 50,
                  offset: int = 0, sort: str = "paper_id", direction: str = "desc",
                  collection_id: str | None = None, tag: str | None = None,
                  source: str | None = None) -> dict:
    """服务端检索（借鉴 Zotero local API 的 q/limit/start/sort/tag/collection）。"""
    where = ["i.deleted_at IS NULL"]
    params: list = []
    if q:
        # FTS5 优先；查询语法异常时回退 LIKE
        try:
            rows = conn.execute(
                "SELECT paper_id FROM items_fts WHERE items_fts MATCH ? LIMIT 500",
                (_fts_query(q),)).fetchall()
            # 注意：FTS5 若建成 contentless（content=''）则读列恒为 NULL，
            # 这里过滤掉 None，并在为空时回退 LIKE，避免「有匹配却查不到」。
            ids = [r["paper_id"] for r in rows if r["paper_id"]]
            if ids:
                where.append(f"i.paper_id IN ({','.join('?' * len(ids))})")
                params.extend(ids)
            else:
                where.append(
                    "(i.title LIKE ? OR i.abstract LIKE ? OR i.authors_json LIKE ?)")
                params.extend([f"%{q}%"] * 3)
        except Exception:
            where.append("(i.title LIKE ? OR i.abstract LIKE ? OR i.authors_json LIKE ?)")
            params.extend([f"%{q}%"] * 3)
    if collection_id:
        where.append(
            "EXISTS(SELECT 1 FROM collection_items ci WHERE ci.paper_id=i.paper_id"
            " AND ci.collection_id=?)")
        params.append(collection_id)
    if tag:
        where.append(
            "EXISTS(SELECT 1 FROM item_tags it WHERE it.paper_id=i.paper_id AND it.tag=?)")
        params.append(tag)
    if source:
        where.append("i.source=?")
        params.append(source)
    clause = " AND ".join(where)
    col = _SORT_COLUMNS.get(sort, _SORT_COLUMNS["paper_id"])
    order = "ASC" if str(direction).lower() == "asc" else "DESC"
    # NULL 一律排最后（否则 year 全空的条目会霸占「按年份倒序」的首页）
    nulls_last = f"({col} IS NULL), {col} {order}"
    total = conn.execute(
        f"SELECT COUNT(*) c FROM items i WHERE {clause}", params).fetchone()["c"]
    rows = conn.execute(
        f"SELECT i.paper_id,i.title,i.journal,i.year,i.doi,i.source,i.added_at"
        f" FROM items i WHERE {clause} ORDER BY {nulls_last} LIMIT ? OFFSET ?",
        [*params, max(1, min(int(limit or 50), 500)), max(0, int(offset or 0))]).fetchall()
    return {"total": int(total), "count": len(rows), "offset": int(offset or 0),
            "limit": int(limit or 50),
            "papers": [dict(r) for r in rows]}


def _fts_query(q: str) -> str:
    """把用户输入转成安全的 FTS5 查询（按词 OR，去引号）。"""
    tokens = [t for t in str(q).replace('"', " ").split() if t]
    return " OR ".join(f'"{t}"' for t in tokens) or '""'


# ---------------------------------------------------------------- 集合 / 标签

def list_collections(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT c.collection_id,c.name,c.parent_id,c.order_index,"
        " (SELECT COUNT(*) FROM collection_items ci WHERE ci.collection_id=c.collection_id)"
        " AS count FROM collections c WHERE c.deleted_at IS NULL"
        " ORDER BY c.order_index, c.name").fetchall()
    out = [dict(r) for r in rows]
    # 未分类计数（便于三栏 UI 显示）
    uncategorized = conn.execute(
        "SELECT COUNT(*) c FROM items i WHERE i.deleted_at IS NULL AND NOT EXISTS("
        " SELECT 1 FROM collection_items ci WHERE ci.paper_id=i.paper_id)").fetchone()["c"]
    out.append({"collection_id": "", "name": "未分类", "parent_id": None,
                "order_index": 999, "count": int(uncategorized)})
    return out


def create_collection(conn: sqlite3.Connection, name: str,
                      parent_id: str | None = None) -> dict:
    name = (name or "").strip()
    if not name:
        raise ValueError("集合名不能为空")
    cid = f"col_{uuid.uuid4().hex[:10]}"
    idx = conn.execute("SELECT COALESCE(MAX(order_index),0)+1 n FROM collections"
                       ).fetchone()["n"]
    conn.execute(
        "INSERT INTO collections(collection_id,name,parent_id,order_index,created_at)"
        " VALUES(?,?,?,?,?)", (cid, name, parent_id, idx, _now()))
    conn.commit()
    return {"collection_id": cid, "name": name, "parent_id": parent_id,
            "order_index": int(idx)}


def rename_collection(conn: sqlite3.Connection, collection_id: str,
                      name: str) -> dict | None:
    name = (name or "").strip()
    if not name:
        raise ValueError("集合名不能为空")
    cur = conn.execute("UPDATE collections SET name=? WHERE collection_id=?"
                       " AND deleted_at IS NULL", (name, collection_id))
    conn.commit()
    return {"collection_id": collection_id, "name": name} if cur.rowcount else None


def delete_collection(conn: sqlite3.Connection, collection_id: str) -> bool:
    cur = conn.execute("UPDATE collections SET deleted_at=? WHERE collection_id=?"
                       " AND deleted_at IS NULL", (_now(), collection_id))
    conn.execute("DELETE FROM collection_items WHERE collection_id=?", (collection_id,))
    conn.commit()
    return bool(cur.rowcount)


def assign_collection(conn: sqlite3.Connection, paper_id: str,
                      collection_id: str, *, add: bool = True) -> bool:
    if add:
        conn.execute(
            "INSERT OR IGNORE INTO collection_items(collection_id,paper_id,"
            "order_index,added_at) VALUES(?,?,0,?)",
            (collection_id, str(paper_id), _now()))
    else:
        conn.execute("DELETE FROM collection_items WHERE collection_id=? AND paper_id=?",
                     (collection_id, str(paper_id)))
    conn.commit()
    return add


def list_tags(conn: sqlite3.Connection) -> list[dict]:
    """标签及计数（**按标签聚合**，type 以逗号给出，避免 manual/auto 把计数拆成两组）。"""
    rows = conn.execute(
        "SELECT tag AS name, COUNT(*) AS count,"
        " GROUP_CONCAT(DISTINCT type) AS types FROM item_tags"
        " GROUP BY tag ORDER BY count DESC, tag").fetchall()
    out = []
    for r in rows:
        item = dict(r)
        item["type"] = (item.pop("types") or "manual")
        out.append(item)
    return out


def set_tag(conn: sqlite3.Connection, paper_id: str, tag: str, *,
            add: bool = True, type_: str = "manual") -> bool:
    tag = (tag or "").strip()
    if not tag:
        raise ValueError("标签不能为空")
    if add:
        conn.execute("INSERT OR IGNORE INTO tags(name,type) VALUES(?,?)", (tag, type_))
        conn.execute(
            "INSERT OR REPLACE INTO item_tags(paper_id,tag,type,added_at)"
            " VALUES(?,?,?,?)", (str(paper_id), tag, type_, _now()))
        _fts_refresh(conn, str(paper_id))
    else:
        conn.execute("DELETE FROM item_tags WHERE paper_id=? AND tag=?",
                     (str(paper_id), tag))
        _fts_refresh(conn, str(paper_id))
    conn.commit()
    return add


# ---------------------------------------------------------------- 笔记

def list_notes(conn: sqlite3.Connection, paper_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM notes WHERE item_id=(SELECT id FROM items WHERE paper_id=?)"
        " ORDER BY created_at DESC", (str(paper_id),)).fetchall()
    return [dict(r) for r in rows]


def add_note(conn: sqlite3.Connection, paper_id: str, content_md: str, *,
             title: str = "", source: str = "user",
             parent_note_id: str | None = None) -> dict:
    item_id = _item_id(conn, paper_id)
    if item_id is None:
        raise ValueError(f"文献不存在: {paper_id}")
    note_id = f"note_{uuid.uuid4().hex[:12]}"
    now = _now()
    conn.execute(
        "INSERT INTO notes(note_id,item_id,parent_note_id,title,content_md,source,"
        "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
        (note_id, item_id, parent_note_id, title, content_md, source, now, now))
    conn.commit()
    return {"note_id": note_id, "paper_id": str(paper_id), "title": title,
            "content_md": content_md, "source": source, "created_at": now}


def update_note(conn: sqlite3.Connection, note_id: str, *, title: str | None = None,
                content_md: str | None = None) -> dict | None:
    row = conn.execute("SELECT * FROM notes WHERE note_id=?", (note_id,)).fetchone()
    if row is None:
        return None
    conn.execute(
        "UPDATE notes SET title=COALESCE(?,title), content_md=COALESCE(?,content_md),"
        " updated_at=? WHERE note_id=?",
        (title, content_md, _now(), note_id))
    conn.commit()
    return dict(conn.execute("SELECT * FROM notes WHERE note_id=?", (note_id,)).fetchone())


def delete_note(conn: sqlite3.Connection, note_id: str) -> bool:
    cur = conn.execute("DELETE FROM notes WHERE note_id=?", (note_id,))
    conn.commit()
    return bool(cur.rowcount)


# ---------------------------------------------------------------- 关系 / 统计

def add_relation(conn: sqlite3.Connection, subject: str, predicate: str, object_: str,
                 *, source: str = "", confidence: float | None = None,
                 meta: dict | None = None) -> bool:
    """幂等写入关系（借鉴 Zotero itemRelations）。返回是否新插入。"""
    cur = conn.execute(
        "INSERT OR IGNORE INTO relations(subject,predicate,object,source,confidence,"
        "meta_json,created_at) VALUES(?,?,?,?,?,?,?)",
        (subject, predicate, object_, source, confidence,
         json.dumps(meta or {}, ensure_ascii=False, default=str), _now()))
    conn.commit()
    return bool(cur.rowcount)


def list_relations(conn: sqlite3.Connection, *, subject: str | None = None,
                   object_: str | None = None,
                   predicate: str | None = None) -> list[dict]:
    where, params = ["1=1"], []
    if subject:
        where.append("subject=?")
        params.append(subject)
    if object_:
        where.append("object=?")
        params.append(object_)
    if predicate:
        where.append("predicate=?")
        params.append(predicate)
    rows = conn.execute(
        f"SELECT subject,predicate,object,source,confidence,meta_json,created_at"
        f" FROM relations WHERE {' AND '.join(where)} ORDER BY confidence DESC NULLS LAST",
        params).fetchall()
    return [dict(r) for r in rows]


def search_metrics(conn: sqlite3.Connection, *, name: str = "",
                   min_value: float | None = None,
                   max_value: float | None = None,
                   technique: str | None = None, limit: int = 100) -> list[dict]:
    """跨文献数值检索（`entry_metrics` 的价值所在）。"""
    where, params = ["1=1"], []
    if name:
        where.append("m.name LIKE ?")
        params.append(f"%{name}%")
    if min_value is not None:
        where.append("m.value >= ?")
        params.append(float(min_value))
    if max_value is not None:
        where.append("m.value <= ?")
        params.append(float(max_value))
    if technique:
        where.append("m.technique=?")
        params.append(technique)
    rows = conn.execute(
        f"SELECT m.name,m.value,m.unit,m.technique,m.entry_id,"
        f" COALESCE(i.paper_id, e.paper_id) AS paper_id,"
        f" COALESCE(i.title, '') AS title"
        f" FROM entry_metrics m"
        f" LEFT JOIN items i ON i.id=m.item_id"
        f" LEFT JOIN entries e ON e.entry_id=m.entry_id"
        f" WHERE {' AND '.join(where)} ORDER BY m.value DESC LIMIT ?",
        [*params, max(1, min(int(limit or 100), 1000))]).fetchall()
    return [dict(r) for r in rows]


def stats(conn: sqlite3.Connection) -> dict:
    def one(sql: str) -> int:
        return int(conn.execute(sql).fetchone()[0])
    return {
        "papers": one("SELECT COUNT(*) FROM items WHERE deleted_at IS NULL"),
        "attachments": one("SELECT COUNT(*) FROM attachments"),
        "figures": one("SELECT COUNT(*) FROM figures"),
        "entries": one("SELECT COUNT(*) FROM entries"),
        "metrics": one("SELECT COUNT(*) FROM entry_metrics"),
        "notes": one("SELECT COUNT(*) FROM notes"),
        "collections": one("SELECT COUNT(*) FROM collections WHERE deleted_at IS NULL"),
        "tags": one("SELECT COUNT(DISTINCT tag) FROM item_tags"),
        "relations": one("SELECT COUNT(*) FROM relations"),
        "db": str(db_path()),
        "size_kb": round(db_path().stat().st_size / 1024, 1) if db_path().is_file() else 0,
    }


def rebuild(*, backup: bool = True) -> dict:
    """丢弃并用文件重建 DB（镜像层可随时重建，不影响既有功能）。

    注意：Windows 下若有连接未关闭，替换文件会 `[WinError 32]`。
    调用方（或 API）需确保没有活跃连接；失败时给出明确提示而非静默。
    """
    target = db_path()
    old = None
    if target.is_file():
        old = target.with_suffix(f".db.old-{time.strftime('%Y%m%d-%H%M%S')}")
        try:
            target.replace(old)
        except OSError as exc:
            raise RuntimeError(
                f"无法替换 DB 文件（可能仍有连接未关闭）：{exc}") from exc
        for suffix in ("-wal", "-shm"):
            side = Path(str(target) + suffix)
            if side.is_file():
                try:
                    side.unlink()
                except OSError:
                    pass
    reset_ready_cache()
    status = ensure_ready(auto_migrate=True)
    status["replaced"] = str(old) if old else None
    return status
