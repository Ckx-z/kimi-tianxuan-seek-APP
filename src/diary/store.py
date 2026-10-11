"""科研日记存储（v2.2.0）：Markdown 正文 + 结构化 @ 实验记录关联。

落盘（**JSONL**，与 `literature/knowledge.py` 同一先例）：
    runtime_config.user_data_root()/diary/diary_entries.jsonl
开发态即「仓库根/data/diary/diary_entries.jsonl」，frozen 打包态为
「%APPDATA%/COF-Film-Recommend/data/diary/diary_entries.jsonl」；
路径每次调用重算（`diary_path()`），因此测试 monkeypatch
`runtime_config.user_data_root` 即可完全隔离，不需要重载模块。

条目 schema（每行一个 JSON 对象）：
    {
      "entry_id": "dy_<12 位十六进制>",     # 本模块生成，客户端不可指定
      "date": "2026-09-10",                 # 日记日期（YYYY-MM-DD，缺省=今天）
      "title": "标题",                       # 可空（正文非空即可）
      "content": "# Markdown 正文",          # 原样保存，渲染交给前端 react-markdown
      "mentions": ["rec_20260902_006", ...], # 结构化 @ 关联（实验记录 id，去重保序）
      "created_at": "2026-09-10T21:03:11+08:00",
      "updated_at": "2026-09-10T21:03:11+08:00"
    }

写入原子性：整表读改写 —— 先写 `diary_entries.jsonl.tmp` 再 `tmp.replace(path)`
（与 `src/dft/jobs.py` / `literature/knowledge.py` 同写法），进程内加锁。

@ 关联（mentions）语义 —— 与实验记录模块**只读**耦合：
- 新增的 mention 必须是**已存在**的实验记录 id（形如 rec_YYYYMMDD_NNN）；
  不存在 / 格式非法一律 ValueError（中文原因），不静默丢弃，避免日记里
  留下"看起来能点其实没有"的假引用；
- 已保存过的 mention 不因实验记录后来被删除而失效：更新日记时只对
  **本次新增**的 mention 做存在性校验，历史引用原样保留，由
  `mention_details()` 标记 `exists=False` 供前端显示「记录已删除」；
- 实验记录索引读取失败（异常）时跳过存在性校验而不是全盘拒绝，
  避免"实验记录模块一坏，日记就写不进去"。
"""

from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from datetime import date as _date
from datetime import datetime
from pathlib import Path

try:
    from src import runtime_config
except ImportError:  # pragma: no cover - src 直接在 sys.path 时
    import runtime_config  # type: ignore

logger = logging.getLogger(__name__)

DIARY_SUBDIR = "diary"
DIARY_FILENAME = "diary_entries.jsonl"

# 日记 id：dy_ + 12 位十六进制（与 knowledge 的 ke_ 同风格）
ID_RE = re.compile(r"^dy_[0-9a-f]{12}$")
# 实验记录 id：rec_YYYYMMDD_NNN（与 src/records/store.py 的 _ID_RE 一致）
RECORD_ID_RE = re.compile(r"^rec_\d{8}_\d{3}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

MAX_CONTENT_CHARS = 50_000   # 正文上限（超长直接拒绝，不做静默截断丢用户内容）
MAX_TITLE_CHARS = 200
MAX_MENTIONS = 50

_lock = threading.Lock()


# --------------------------------------------------------------------- 路径

def diary_dir() -> Path:
    """日记目录（每次调用重算，测试可 monkeypatch user_data_root）。"""
    return runtime_config.user_data_root() / DIARY_SUBDIR


def diary_path() -> Path:
    """日记 JSONL 文件路径。"""
    return diary_dir() / DIARY_FILENAME


# ----------------------------------------------------------------- 基础读写

def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _today() -> str:
    return _date.today().isoformat()


def _new_id() -> str:
    return f"dy_{uuid.uuid4().hex[:12]}"


def _clean(v) -> str:
    return str(v or "").strip()


def _load() -> list[dict]:
    """读整表；空行 / 损坏行跳过（不让一行坏数据毁掉整个日记本）。

    缺字段的行就地补默认值（手改过 / 旧版本写的文件也能正常读）。
    """
    path = diary_path()
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
                logger.warning("日记行解析失败（已跳过）: %s", line[:80])
                continue
            if isinstance(obj, dict) and obj.get("entry_id"):
                obj.setdefault("date", "")
                obj.setdefault("title", "")
                obj.setdefault("content", "")
                if not isinstance(obj.get("mentions"), list):
                    obj["mentions"] = []
                out.append(obj)
    except Exception as exc:
        logger.warning("日记读取失败（按空处理）: %s", exc)
    return out


def _save(rows: list[dict]) -> None:
    """整表原子落盘（tmp + replace）。"""
    path = diary_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    tmp.replace(path)


# ------------------------------------------------------- 实验记录索引（只读）

def _record_info(rec: dict) -> dict:
    ald = rec.get("aldehyde") or {}
    amine = rec.get("amine") or {}
    label = (f"{ald.get('name') or ald.get('smiles') or '?'}"
             f" + {amine.get('name') or amine.get('smiles') or '?'}")
    return {
        "record_id": _clean(rec.get("record_id")),
        "experiment_no": _clean(rec.get("experiment_no")),
        "label": label,
        "outcome": _clean(rec.get("outcome")),
        "date": _clean(rec.get("experiment_date") or rec.get("date")),
    }


def record_index() -> dict[str, dict] | None:
    """{record_id: 摘要}（实验记录模块**只读**调用）；失败返回 None。

    None 表示"索引不可用"（读取异常），调用方据此**跳过**存在性校验，
    而不是把所有 @ 当成不存在。
    """
    try:
        try:
            from src.records import store as rec_store
        except ImportError:  # pragma: no cover
            from records import store as rec_store  # type: ignore
        recs = rec_store.list_records()
    except Exception as exc:
        logger.warning("实验记录索引读取失败（跳过 @ 存在性校验）: %s", exc)
        return None
    return {str(r.get("record_id")): _record_info(r) for r in recs}


def mention_details(entry: dict,
                    index: dict[str, dict] | None = None) -> list[dict]:
    """条目的 @ 关联展开（含实验记录摘要与 exists 标记）。"""
    idx = record_index() if index is None else index
    out: list[dict] = []
    for rid in entry.get("mentions") or []:
        rid = str(rid)
        info = (idx or {}).get(rid)
        item = {"record_id": rid, "exists": info is not None}
        if info:
            item.update({k: v for k, v in info.items() if k != "record_id"})
        out.append(item)
    return out


def with_mentions(entry: dict,
                  index: dict[str, dict] | None = None) -> dict:
    """条目副本 + mention_details（API/前端用，不落盘）。"""
    return {**entry, "mention_details": mention_details(entry, index)}


# --------------------------------------------------------------- 字段校验

def parse_date(value, field: str = "日期") -> str:
    """日期字符串 → YYYY-MM-DD；格式/真实日期非法均 ValueError（中文）。"""
    raw = _clean(value)
    if not raw:
        raise ValueError(f"{field}不能为空（应为 YYYY-MM-DD）")
    if not DATE_RE.match(raw):
        raise ValueError(f"{field}格式非法：{raw}（应为 YYYY-MM-DD，如 2026-09-10）")
    try:
        datetime.strptime(raw, "%Y-%m-%d")
    except ValueError:
        raise ValueError(f"{field}不是真实存在的日期：{raw}")
    return raw


def _optional_date(value, field: str) -> str:
    raw = _clean(value)
    return parse_date(raw, field) if raw else ""


def _clean_mentions(raw, *, existing: list[str], index) -> list[str]:
    """mentions 规范化 + 校验（去重保序；新增项必须真实存在）。"""
    if raw is None:
        return list(existing)
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        raise ValueError("mentions 必须是实验记录 id 的数组（如 "
                         '["rec_20260902_006"]）')
    out: list[str] = []
    for item in raw:
        rid = _clean(item)
        if not rid:
            continue
        if not RECORD_ID_RE.match(rid):
            raise ValueError(f"实验记录 id 格式非法：{rid}"
                             "（应形如 rec_20260902_006）")
        if rid not in out:
            out.append(rid)
    if len(out) > MAX_MENTIONS:
        raise ValueError(f"单篇日记最多关联 {MAX_MENTIONS} 条实验记录，"
                         f"当前 {len(out)} 条")
    if index is not None:
        keep = set(existing)  # 历史引用即使记录已删也保留
        missing = [r for r in out if r not in index and r not in keep]
        if missing:
            raise ValueError("关联的实验记录不存在："
                             + "、".join(missing)
                             + "（请先在实验记录页创建，或取消该关联）")
    return out


def _validate(payload: dict, *, existing: dict | None = None,
              index=None) -> dict:
    """校验并规范化；existing 非 None 时按"局部更新"合并（None 值不改动）。"""
    base = dict(existing or {})
    for k, v in (payload or {}).items():
        if v is not None:
            base[k] = v

    raw_date = _clean(base.get("date"))
    day = parse_date(raw_date, "日期") if raw_date else _today()

    title = _clean(base.get("title"))
    if len(title) > MAX_TITLE_CHARS:
        raise ValueError(f"日记标题超长：{len(title)} 字，上限 {MAX_TITLE_CHARS} 字")

    content = str(base.get("content") or "")
    if not content.strip():
        raise ValueError("日记正文不能为空（Markdown 原文至少要有 1 个可见字符）")
    if len(content) > MAX_CONTENT_CHARS:
        raise ValueError(f"日记正文超长：{len(content)} 字，"
                         f"上限 {MAX_CONTENT_CHARS} 字（请拆分或精简）")

    mentions = _clean_mentions(base.get("mentions"),
                               existing=list(existing.get("mentions") or [])
                               if existing else [],
                               index=index)
    return {"date": day, "title": title, "content": content.strip(),
            "mentions": mentions}


def _require_id(entry_id) -> str:
    eid = _clean(entry_id)
    if not ID_RE.match(eid):
        raise ValueError(f"日记 id 格式非法：{eid or '（空）'}"
                         "（应为 dy_ + 12 位十六进制）")
    return eid


# --------------------------------------------------------------- 对外 CRUD

def create_entry(date: str = "", title: str = "", content: str = "",
                 mentions=None) -> dict:
    """新建日记，返回落盘条目（含 entry_id / created_at / updated_at）。"""
    index = record_index()
    fields = _validate({"date": date or _today(), "title": title,
                        "content": content, "mentions": mentions},
                       index=index)
    now = _now()
    entry = {"entry_id": _new_id(), **fields,
             "created_at": now, "updated_at": now}
    with _lock:
        rows = _load()
        rows.append(entry)
        _save(rows)
    return dict(entry)


def get_entry(entry_id: str) -> dict | None:
    """按 id 取条目；格式非法 ValueError，不存在返回 None。"""
    eid = _require_id(entry_id)
    for r in _load():
        if r.get("entry_id") == eid:
            return dict(r)
    return None


def _haystack(entry: dict, index: dict[str, dict] | None) -> str:
    parts = [str(entry.get("title") or ""), str(entry.get("content") or "")]
    for rid in entry.get("mentions") or []:
        rid = str(rid)
        parts.append(rid)
        info = (index or {}).get(rid)
        if info:
            parts.extend([str(info.get("experiment_no") or ""),
                          str(info.get("label") or ""),
                          str(info.get("outcome") or "")])
    return "\n".join(parts).casefold()


def list_entries(keyword: str = "", date_from: str = "", date_to: str = "",
                 record_id: str = "", limit: int | None = None) -> list[dict]:
    """检索日记（按日期倒序，同日按创建时间倒序）。

    - keyword：按空白拆词，**全部命中**才算匹配；匹配 标题 + 正文 +
      mentions 里的记录 id + 关联记录（实验编号 / 单体名 / 结果）；
    - date_from / date_to：含端点，YYYY-MM-DD；
    - record_id：只看 @ 了该实验记录的日记（精确匹配）；
    - limit：>0 时截取前 N 条。
    参数非法一律 ValueError（中文原因）。
    """
    tokens = [t.casefold() for t in _clean(keyword).split() if t]
    df = _optional_date(date_from, "date_from（起始日期）")
    dt = _optional_date(date_to, "date_to（结束日期）")
    if df and dt and df > dt:
        raise ValueError(f"日期范围非法：起始 {df} 晚于结束 {dt}")
    rid = _clean(record_id)
    if rid and not RECORD_ID_RE.match(rid):
        raise ValueError(f"实验记录 id 格式非法：{rid}"
                         "（应形如 rec_20260902_006）")

    index = record_index() if (tokens or rid) else None
    out: list[dict] = []
    for r in _load():
        day = str(r.get("date") or "")
        if df and day < df:
            continue
        if dt and day > dt:
            continue
        if rid and rid not in [str(x) for x in (r.get("mentions") or [])]:
            continue
        if tokens:
            hay = _haystack(r, index)
            if not all(t in hay for t in tokens):
                continue
        out.append(dict(r))
    out.sort(key=lambda r: (str(r.get("date") or ""),
                            str(r.get("created_at") or "")), reverse=True)
    if limit and limit > 0:
        out = out[:limit]
    return out


def find_by_record(record_id: str, limit: int | None = None) -> list[dict]:
    """按实验记录反查日记（record_id 精确匹配 mentions）。"""
    return list_entries(record_id=record_id, limit=limit)


def update_entry(entry_id: str, fields: dict | None = None,
                 **kwargs) -> dict | None:
    """局部更新（date/title/content/mentions），返回更新后条目；不存在 None。

    只对**本次新增**的 mention 校验存在性，历史引用不因记录被删而失效。
    """
    eid = _require_id(entry_id)
    patch = {k: v for k, v in {**(fields or {}), **kwargs}.items()
             if k in ("date", "title", "content", "mentions")}
    with _lock:
        rows = _load()
        for i, r in enumerate(rows):
            if r.get("entry_id") != eid:
                continue
            index = record_index()
            merged = _validate(patch, existing=r, index=index)
            merged["entry_id"] = eid
            merged["created_at"] = r.get("created_at") or _now()
            merged["updated_at"] = _now()
            rows[i] = merged
            _save(rows)
            return dict(merged)
    return None


def delete_entry(entry_id: str) -> dict | None:
    """删除日记，返回被删条目；不存在 None。"""
    eid = _require_id(entry_id)
    with _lock:
        rows = _load()
        for i, r in enumerate(rows):
            if r.get("entry_id") == eid:
                del rows[i]
                _save(rows)
                return dict(r)
    return None


def stats() -> dict:
    """日记总数 / 最早最晚日期（助手工具与前端概览用）。"""
    rows = _load()
    days = sorted(str(r.get("date") or "") for r in rows)
    return {"total": len(rows),
            "first_date": days[0] if days else "",
            "last_date": days[-1] if days else ""}
