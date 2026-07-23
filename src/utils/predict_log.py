"""预测日志（P1 后端支撑）。

每次查询 append 一条 JSON 到 data/prediction_log.jsonl，
自动补 timestamp 与 schema_version=1。永不抛异常影响主流程。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    from src import runtime_config
except ImportError:
    import runtime_config  # type: ignore

PROJECT_ROOT = runtime_config.resource_root()
LOG_PATH = runtime_config.user_data_root() / "prediction_log.jsonl"

SCHEMA_VERSION = 1


def log_prediction(record: dict) -> None:
    """追加一条预测记录到 JSONL 日志；任何失败均静默。"""
    try:
        if not isinstance(record, dict):
            record = {"raw": str(record)}
        entry = dict(record)
        entry.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
        entry["schema_version"] = SCHEMA_VERSION
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.warning("预测日志写入失败: %s", exc)
