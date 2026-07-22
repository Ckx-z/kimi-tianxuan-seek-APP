"""API 共享依赖：sys.path 引导、预测器单例、内置单体库、主分数口径。

与 app/gradio_app.py 的口径保持一致（D29：max(树, GNN) 乐观召回）；
此处为不依赖 Gradio 的独立实现，规则变更时需与 gradio_app 同步。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

_PREDICTOR = None


def get_predictor():
    """FilmPredictor 惰性单例（模型加载慢，进程内只建一次）。"""
    global _PREDICTOR
    if _PREDICTOR is None:
        from predictor import FilmPredictor
        _PREDICTOR = FilmPredictor()
    return _PREDICTOR


def headline_score(pred_result: dict) -> tuple[float | None, str | None]:
    """主分数 = max(路由树模型分, GNN 分)（D29 口径，与 gradio_app 一致）。

    返回 (score, source)，source ∈ {"both", "tree", "gnn", None}。
    """
    pred_result = pred_result or {}
    tree = pred_result.get("tree_probability")
    gnn = pred_result.get("gnn_probability")
    tree = tree if isinstance(tree, (int, float)) else None
    gnn = gnn if isinstance(gnn, (int, float)) else None
    if tree is not None and gnn is not None:
        return max(tree, gnn), "both"
    if tree is not None:
        return tree, "tree"
    if gnn is not None:
        return gnn, "gnn"
    return None, None


def build_prediction_payload(ald_smiles: str, amine_smiles: str,
                             pred_result: dict, source: str = "api") -> dict:
    """组装 API 响应 + 预测日志记录（契约同 data/prediction_log.jsonl）。

    OOD=out 时 score 置 null（⛔ 优先于打分）；tree/gnn 分量与 std 同步
    置 null，防止 API 消费方绕过 score 直读分量被误导。log_prediction
    直接读本 payload 的分量字段，落盘随之同样置空。
    """
    ood = (pred_result or {}).get("ood") or {}
    ood_out = ood.get("level") == "out"
    score, score_source = headline_score(pred_result)
    return {
        "ald_smiles": ald_smiles,
        "amine_smiles": amine_smiles,
        "score": None if ood_out else score,
        "score_source": score_source,
        "score_policy": "max_tree_gnn",
        "tree_score": None if ood_out else pred_result.get("tree_probability"),
        "tree_std": None if ood_out else pred_result.get("tree_std"),
        "tree_model_name": pred_result.get("tree_model_name"),
        "tree_route": pred_result.get("tree_route"),
        "gnn_score": None if ood_out else pred_result.get("gnn_probability"),
        "gnn_std": None if ood_out else pred_result.get("gnn_std"),
        "ood": ood,
        "source": source,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }


def log_prediction(payload: dict) -> None:
    """预测日志落盘（与 App 共用 data/prediction_log.jsonl，失败静默）。"""
    try:
        from utils.predict_log import log_prediction as _log
        _log({
            "schema_version": "1.0",
            "type": "prediction",
            "ald_smiles": payload["ald_smiles"],
            "amine_smiles": payload["amine_smiles"],
            "score": payload["score"],
            "score_policy": payload["score_policy"],
            "tree_score": payload["tree_score"],
            "gnn_score": payload["gnn_score"],
            "std": payload.get("tree_std"),
            "arm": payload.get("tree_model_name"),
            "route": payload.get("tree_route"),
            "ood_level": (payload.get("ood") or {}).get("level", "none"),
            "model_version": "tree_v4_routed+gnn_v5.3",
            "source": payload.get("source", "api"),
            "timestamp": payload["timestamp"],
        })
    except Exception:
        pass


_MONOMER_CACHE: dict | None = None


def load_builtin_monomers() -> dict:
    """内置单体库：{"aldehydes": [...], "amines": [...], "by_smiles": {...}}。

    读 data/builtin_monomers.json（17 个内置单体，含 name/role/cas/smiles）。
    """
    global _MONOMER_CACHE
    if _MONOMER_CACHE is not None:
        return _MONOMER_CACHE
    path = PROJECT_ROOT / "data" / "builtin_monomers.json"
    items = json.loads(path.read_text(encoding="utf-8"))
    ald = [m for m in items if m.get("role") == "aldehyde"]
    amine = [m for m in items if m.get("role") == "amine"]
    _MONOMER_CACHE = {
        "aldehydes": ald,
        "amines": amine,
        "by_smiles": {m["smiles"]: m for m in items},
        "source": str(path),
    }
    return _MONOMER_CACHE
