"""list_prediction_history 工具：读 prediction_log.jsonl 的打分历史（只读）。

与 GET /api/predict/history 同一数据源（utils/predict_log.LOG_PATH），
新→旧，limit 控制条数。文件不存在 / 无记录时如实说"系统内未查到"。
"""

from __future__ import annotations

import json

_MAX_LIMIT = 50
_MAX_FIELD = 120


def _cut(s: str, n: int = _MAX_FIELD) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[:n] + "…"


def _fmt_entry(rec: dict) -> str:
    ts = str(rec.get("timestamp") or "")[:19].replace("T", " ")
    ald = _cut(str(rec.get("ald_smiles") or "?"), 60)
    amine = _cut(str(rec.get("amine_smiles") or "?"), 60)
    score = rec.get("score")
    score_s = f"{float(score):.3f}" if isinstance(score, (int, float)) else "（无）"
    ood = rec.get("ood_level") or rec.get("ood") or "none"
    ood_s = f"，OOD={ood}" if ood != "none" else ""
    return f"- {ts}｜醛 {ald} / 胺 {amine}｜分数 {score_s}{ood_s}"


def list_prediction_history(limit: int = 10) -> dict:
    """打分历史（新→旧）。limit 默认 10，上限 50。"""
    try:
        try:
            from src.utils import predict_log
        except ImportError:  # pragma: no cover
            from utils import predict_log  # type: ignore
        path = predict_log.LOG_PATH
        entries: list[dict] = []
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if isinstance(rec, dict) and rec.get("type") == "prediction":
                    entries.append(rec)
    except Exception as exc:
        return {"text": f"打分历史读取失败：{type(exc).__name__}: {exc}",
                "details": {}, "is_error": True}

    if not entries:
        return {"text": "系统内未查到打分历史记录。",
                "details": {"count": 0}, "is_error": False}

    entries.reverse()  # 日志按时间追加，反转为新→旧
    limit = max(1, min(int(limit or 10), _MAX_LIMIT))
    shown = entries[:limit]
    text = (f"共 {len(entries)} 条打分历史，以下为最近 {len(shown)} 条：\n"
            + "\n".join(_fmt_entry(e) for e in shown))
    return {
        "text": text,
        "details": {"count": len(entries), "shown": len(shown)},
        "is_error": False,
    }
