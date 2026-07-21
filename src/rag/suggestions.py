"""迭代建议读取（P3 后端，页⑤方案迭代支撑）。

读 data/rag_export/suggestions/sug_<YYYYMMDD>_<NNN>.json —— 由 minimax
RAG 侧写入的迭代建议（schema 见 data/rag_export/README.md Schema 3
suggestion）。App 侧只读不回写。

- 契约示例文件 example.json 不作为真实建议列出；
- 未知 schema_version / 损坏 / 缺主键的文件跳过不崩（契约演进规则）；
- 结果按 created_at 倒序（最新建议在前），同刻按 suggestion_id 倒序。
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SUGGESTIONS_DIR = PROJECT_ROOT / "data" / "rag_export" / "suggestions"

SUPPORTED_SCHEMA_VERSION = "1.0"
RECORD_TYPE = "suggestion"
VALID_TYPES = ("condition_adjust", "new_candidate")  # 条件调整 / 新候选单体对
VALID_STATUS = ("new", "adopted", "rejected", "done")

_ID_RE = re.compile(r"^sug_(\d{8})_(\d{3})$")

# 契约示例文件不作为真实建议列出
_EXAMPLE_FILE = "example.json"


def _read_file(path: Path) -> dict | None:
    """读单个 suggestion 文件；任何解析/校验异常返回 None（跳过不崩）。"""
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("迭代建议读取失败 %s: %s", path.name, exc)
        return None
    if not isinstance(obj, dict):
        return None
    # 未知 schema_version / record_type：按契约跳过而非报错
    if str(obj.get("schema_version", "")) != SUPPORTED_SCHEMA_VERSION:
        logger.warning("迭代建议 schema_version 不支持，跳过 %s", path.name)
        return None
    if obj.get("record_type") != RECORD_TYPE:
        return None
    if not _ID_RE.match(str(obj.get("suggestion_id", ""))):
        return None
    if not isinstance(obj.get("payload"), dict):
        return None
    if not isinstance(obj.get("evidence_refs"), list):
        return None
    return obj


def list_suggestions(favorite_id: str | None = None) -> list[dict]:
    """全部迭代建议（可按 favorite_id 过滤），按 created_at 倒序。

    favorite_id 给定时只返回针对该收藏条目的建议（favorite_id 相同的；
    通用建议 favorite_id=null 不在过滤结果里）。
    """
    if not SUGGESTIONS_DIR.exists():
        return []
    sugs = []
    for p in sorted(SUGGESTIONS_DIR.glob("sug_*.json")):
        if p.name == _EXAMPLE_FILE:
            continue
        sug = _read_file(p)
        if sug is None:
            continue
        if favorite_id is not None and sug.get("favorite_id") != favorite_id:
            continue
        sugs.append(sug)
    sugs.sort(
        key=lambda s: (str(s.get("created_at", "")), str(s.get("suggestion_id", ""))),
        reverse=True,
    )
    return sugs


def get_suggestion(sug_id: str) -> dict | None:
    """按 suggestion_id 取建议；不存在/损坏/不合契约返回 None。"""
    if not sug_id or not isinstance(sug_id, str) or not _ID_RE.match(sug_id):
        return None
    path = SUGGESTIONS_DIR / f"{sug_id}.json"
    return _read_file(path) if path.exists() else None
