"""DFT 计算历史：user_data_root()/dft_log.jsonl（仿 prediction_log 模式）。

每次任务完成（成功或失败）append 一条 JSON；读历史新→旧、limit/offset 分页。
永不抛异常影响主流程。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

try:
    from src import runtime_config
except ImportError:  # pragma: no cover
    import runtime_config  # type: ignore

logger = logging.getLogger(__name__)

LOG_PATH = runtime_config.user_data_root() / "dft_log.jsonl"
SCHEMA_VERSION = 1


def log_dft(record: dict) -> None:
    """追加一条 DFT 计算记录；任何失败静默。"""
    try:
        entry = dict(record) if isinstance(record, dict) else {"raw": str(record)}
        entry.setdefault("type", "dft")
        entry.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
        entry["schema_version"] = SCHEMA_VERSION
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.warning("DFT 历史写入失败: %s", exc)


def read_history(limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
    """读历史（新→旧）；返回 (分页后条目, 总条数)。文件不存在 → ([], 0)。"""
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    entries: list[dict] = []
    try:
        if LOG_PATH.is_file():
            for line in LOG_PATH.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if isinstance(rec, dict) and rec.get("type") == "dft":
                    entries.append(rec)
    except Exception:
        entries = []
    entries.reverse()
    return entries[offset:offset + limit], len(entries)
