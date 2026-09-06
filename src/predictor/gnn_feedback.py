"""GNN 成膜打分反馈库（v1.8.0，需求一）：三通道反馈 → 校验 → 微调数据源。

落盘：user_data_root()/feedback/gnn_feedback.jsonl（追加式，frozen 下自动
落 %APPDATA%/COF-Film-Recommend/data/feedback/）。

行结构：
{"feedback_id", "source": score_correction|literature_pdf|experiment_csv,
 "ald_smiles", "amine_smiles", "label": 0|0.5|1, "note", "refs",
 "can_network": bool, "dedupe": {"in_base": bool, "existing_label": ...},
 "status": pending|confirmed|rejected|conflict, "created_at", "updated_at"}

校验（confirm 时执行）：
- can_network：label>0 但不可成网 → 黄条提示（仍可确认，但明确标注）；
- 去重：组合已在树训练基础集（v6）或本库其他反馈行中出现 → dedupe 标注；
- 冲突：同组合不同标签的 confirmed 反馈并存 → status=conflict。
"""

from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path

try:
    from src import runtime_config
except ImportError:  # pragma: no cover
    import runtime_config  # type: ignore

logger = logging.getLogger(__name__)

FEEDBACK_PATH = runtime_config.user_data_root() / "feedback" / "gnn_feedback.jsonl"

VALID_SOURCES = {"score_correction", "literature_pdf", "experiment_csv"}
VALID_LABELS = {0.0, 0.5, 1.0}
_ID_RE = re.compile(r"^fb_[0-9a-f]{12}$")
_lock = threading.Lock()

# 基础集组合池（tree v6 训练集；惰性加载）
_base_keys: set[tuple[str, str]] | None = None
_base_load_failed = False


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _new_id() -> str:
    return f"fb_{uuid.uuid4().hex[:12]}"


def _load() -> list[dict]:
    out: list[dict] = []
    if not FEEDBACK_PATH.is_file():
        return out
    try:
        for line in FEEDBACK_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if isinstance(obj, dict):
                out.append(obj)
    except Exception as exc:
        logger.warning("反馈库读取失败（按空处理）: %s", exc)
    return out


def _save(rows: list[dict]) -> None:
    FEEDBACK_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = FEEDBACK_PATH.with_name(FEEDBACK_PATH.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    tmp.replace(FEEDBACK_PATH)


def _base_pair_set() -> set[tuple[str, str]]:
    """树训练基础集组合池（v6 CSV；读取失败按空集处理，不影响主流程）。"""
    global _base_keys, _base_load_failed
    if _base_keys is not None or _base_load_failed:
        return _base_keys or set()
    path = runtime_config.resource_root() / "data" / "interim" / "v6_train_stage1.csv"
    keys: set[tuple[str, str]] = set()
    try:
        import csv
        with open(path, "r", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                keys.add((str(r.get("aldehyde_smiles") or ""),
                          str(r.get("amine_smiles") or "")))
        _base_keys = keys
    except Exception as exc:
        logger.warning("基础集组合池加载失败（按空处理）: %s", exc)
        _base_load_failed = True
    return keys


def _check_can_network(ald: str, amine: str) -> bool:
    try:
        from src.predictor.ood import check_networkability
    except ImportError:  # pragma: no cover
        from predictor.ood import check_networkability  # type: ignore
    res = check_networkability(ald, amine)
    details = res.get("details") or {}
    return bool(details.get("can_network", False))


def submit(ald_smiles: str, amine_smiles: str, label: float,
           note: str = "", source: str = "score_correction",
           refs: list[str] | None = None) -> dict:
    """提交反馈（pending）。SMILES/标签/来源校验失败抛 ValueError。"""
    ald = (ald_smiles or "").strip()
    amine = (amine_smiles or "").strip()
    if not ald or not amine:
        raise ValueError("醛/胺 SMILES 不能为空")
    if float(label) not in VALID_LABELS:
        raise ValueError(f"label 必须是 {sorted(VALID_LABELS)} 之一")
    if source not in VALID_SOURCES:
        raise ValueError(f"source 必须是 {sorted(VALID_SOURCES)} 之一")
    rec = {
        "feedback_id": _new_id(),
        "source": source,
        "ald_smiles": ald,
        "amine_smiles": amine,
        "label": float(label),
        "note": (note or "").strip(),
        "refs": [str(x).strip() for x in (refs or []) if str(x).strip()],
        "can_network": _check_can_network(ald, amine),
        "dedupe": {},
        "status": "pending",
        "created_at": _now(),
        "updated_at": _now(),
    }
    with _lock:
        rows = _load()
        rows.append(rec)
        _save(rows)
    return rec


def list_feedback(status: str | None = None) -> list[dict]:
    out = _load()
    if status:
        out = [r for r in out if r.get("status") == status]
    out.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return out


def get_feedback(feedback_id: str) -> dict | None:
    if not _ID_RE.match(feedback_id or ""):
        return None
    for r in _load():
        if r.get("feedback_id") == feedback_id:
            return dict(r)
    return None


def update_feedback(feedback_id: str, label: float | None = None,
                    note: str | None = None) -> dict | None:
    """改标签/理由（仅 pending/conflict 态可改）。"""
    if label is not None and float(label) not in VALID_LABELS:
        raise ValueError(f"label 必须是 {sorted(VALID_LABELS)} 之一")
    with _lock:
        rows = _load()
        for r in rows:
            if r.get("feedback_id") != feedback_id:
                continue
            if r.get("status") not in ("pending", "conflict"):
                return None
            if label is not None:
                r["label"] = float(label)
            if note is not None:
                r["note"] = (note or "").strip()
            r["updated_at"] = _now()
            _save(rows)
            return dict(r)
    return None


def delete_feedback(feedback_id: str) -> bool:
    with _lock:
        rows = _load()
        kept = [r for r in rows if r.get("feedback_id") != feedback_id]
        if len(kept) == len(rows):
            return False
        _save(kept)
        return True


def confirm(feedback_id: str) -> dict | None:
    """校验并确认（dedupe/can_network/冲突检查 → confirmed/conflict）。"""
    with _lock:
        rows = _load()
        target = next((r for r in rows if r.get("feedback_id") == feedback_id), None)
        if target is None:
            return None
        if target.get("status") not in ("pending", "conflict"):
            return target
        key = (target["ald_smiles"], target["amine_smiles"])
        base = _base_pair_set()
        dedupe = {"in_base": key in base}
        # 与本库其他 confirmed 行冲突检测
        conflict = False
        for r in rows:
            if r.get("feedback_id") == feedback_id or r.get("status") != "confirmed":
                continue
            if (r["ald_smiles"], r["amine_smiles"]) == key \
                    and float(r["label"]) != float(target["label"]):
                conflict = True
                dedupe["existing_label"] = float(r["label"])
                break
        target["dedupe"] = dedupe
        target["status"] = "conflict" if conflict else "confirmed"
        target["updated_at"] = _now()
        _save(rows)
        return dict(target)


def reject(feedback_id: str) -> dict | None:
    """拒绝反馈（不进训练）。"""
    with _lock:
        rows = _load()
        for r in rows:
            if r.get("feedback_id") != feedback_id:
                continue
            if r.get("status") not in ("pending", "conflict"):
                return None
            r["status"] = "rejected"
            r["updated_at"] = _now()
            _save(rows)
            return dict(r)
    return None


def confirmed_rows() -> list[dict]:
    """已确认的反馈行（微调数据源）。"""
    return [r for r in _load() if r.get("status") == "confirmed"]


def export_feedback_csv(path: Path | None = None) -> tuple[Path, int]:
    """confirmed 行导出为训练 CSV（aldehyde_smiles,amine_smiles,is_film）。"""
    rows = confirmed_rows()
    target = path or (FEEDBACK_PATH.parent / "gnn_feedback_confirmed.csv")
    target.parent.mkdir(parents=True, exist_ok=True)
    import csv
    with open(target, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f, fieldnames=["aldehyde_smiles", "amine_smiles", "is_film"])
        writer.writeheader()
        for r in rows:
            writer.writerow({
                "aldehyde_smiles": r["ald_smiles"],
                "amine_smiles": r["amine_smiles"],
                "is_film": f"{r['label']:g}",
            })
    return target, len(rows)
