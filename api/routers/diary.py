"""科研日记路由（v2.2.0）：列表 / 创建 / 更新 / 删除 / 按实验记录反查。

落盘见 `src/diary/store.py`（JSONL，原子写）。实验记录侧仅**只读**引用：
@ 关联只存 record_id，读取时按需回查实验记录给出编号/单体摘要，
不在本模块改任何实验记录。

请求模型就地定义（api/schemas.py 是多批次共享文件，本批次不碰）。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/diary", tags=["diary"])


def _store():
    try:
        from src.diary import store
    except ImportError:  # pragma: no cover - src 直接在 sys.path 时
        from diary import store  # type: ignore
    return store


class DiaryCreate(BaseModel):
    """新建日记（content 必填非空；date 缺省为今天）。"""

    date: str = Field("", description="日记日期 YYYY-MM-DD（缺省今天）")
    title: str = Field("", description="标题（可空）")
    content: str = Field("", description="Markdown 正文（必填非空）")
    mentions: list[str] = Field(
        default_factory=list,
        description='@ 的实验记录 id 列表，如 ["rec_20260902_006"]')


class DiaryUpdate(BaseModel):
    """局部更新（只传要改的字段）。"""

    date: str | None = Field(None, description="日记日期 YYYY-MM-DD")
    title: str | None = Field(None, description="标题")
    content: str | None = Field(None, description="Markdown 正文")
    mentions: list[str] | None = Field(None, description="实验记录 id 列表")


def _with_mentions(entry: dict, index=None) -> dict:
    return _store().with_mentions(entry, index)


@router.get("")
def list_diary(
    keyword: str = Query("", description="关键词（空格分词，全部命中）"),
    date_from: str = Query("", description="起始日期 YYYY-MM-DD（含）"),
    date_to: str = Query("", description="结束日期 YYYY-MM-DD（含）"),
    record_id: str = Query("", description="只看 @ 了该实验记录的日记"),
    limit: int | None = Query(None, description="最多返回条数（缺省全部）"),
):
    """日记列表（按日期倒序），支持关键词 / 日期范围 / 关联记录过滤。"""
    store = _store()
    try:
        entries = store.list_entries(keyword=keyword, date_from=date_from,
                                     date_to=date_to, record_id=record_id,
                                     limit=limit)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"日记读取失败：{type(exc).__name__}: {exc}")
    index = store.record_index() if entries else {}
    return {"entries": [_with_mentions(e, index) for e in entries],
            "count": len(entries), "stats": store.stats()}


@router.post("", status_code=201)
def create_diary(req: DiaryCreate):
    store = _store()
    try:
        entry = store.create_entry(date=req.date, title=req.title,
                                   content=req.content, mentions=req.mentions)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"日记保存失败：{type(exc).__name__}: {exc}")
    return _with_mentions(entry)


@router.get("/by-record/{record_id}")
def list_by_record(record_id: str,
                   limit: int | None = Query(None, description="最多返回条数")):
    """按实验记录反查：所有 @ 了该记录的日记（日期倒序）。"""
    store = _store()
    try:
        entries = store.find_by_record(record_id, limit=limit)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"日记读取失败：{type(exc).__name__}: {exc}")
    index = store.record_index() if entries else {}
    return {"record_id": record_id, "count": len(entries),
            "entries": [_with_mentions(e, index) for e in entries]}


@router.get("/{entry_id}")
def get_diary(entry_id: str):
    store = _store()
    try:
        entry = store.get_entry(entry_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if entry is None:
        raise HTTPException(404, f"日记 {entry_id} 不存在")
    return _with_mentions(entry)


@router.put("/{entry_id}")
def update_diary(entry_id: str, req: DiaryUpdate):
    """局部更新；只传要改的字段（mentions 传 [] 表示清空关联）。"""
    store = _store()
    fields = req.model_dump(exclude_unset=True, exclude_none=True)
    try:
        if not fields:
            entry = store.get_entry(entry_id)
            if entry is None:
                raise HTTPException(404, f"日记 {entry_id} 不存在")
            return _with_mentions(entry)
        entry = store.update_entry(entry_id, fields)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"日记更新失败：{type(exc).__name__}: {exc}")
    if entry is None:
        raise HTTPException(404, f"日记 {entry_id} 不存在")
    return _with_mentions(entry)


@router.delete("/{entry_id}")
def delete_diary(entry_id: str):
    store = _store()
    try:
        entry = store.delete_entry(entry_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"日记删除失败：{type(exc).__name__}: {exc}")
    if entry is None:
        raise HTTPException(404, f"日记 {entry_id} 不存在")
    return {"deleted": entry_id, "entry": entry}
