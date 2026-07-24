"""打分路由：单对 / 批量预测。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..deps import (build_prediction_payload, get_predictor, log_prediction)
from ..schemas import BatchPredictRequest, PredictRequest

router = APIRouter(prefix="/api", tags=["predict"])


def _predict_one(ald: str, amine: str, source: str) -> dict:
    pred = get_predictor()
    result = pred.predict(ald, amine)
    payload = build_prediction_payload(ald, amine, result, source)
    log_prediction(payload)
    # 回写匹配收藏的最新打分快照（页① React 链路此前只写 prediction_log，
    # 导致收藏页误显「未打分」；失败静默，不影响打分主流程）
    try:
        from favorites import store as _fav_store
        _fav_store.update_snapshot_for_pair(ald, amine, {
            "score": payload.get("score"),
            "std": payload.get("tree_std"),
            "arm": payload.get("tree_model_name") or "",
            "ood": payload.get("ood"),
            "score_policy": payload.get("score_policy"),
            "tree_score": payload.get("tree_score"),
            "gnn_score": payload.get("gnn_score"),
        })
    except Exception:
        pass
    payload["explanation"] = _build_explanation(pred, ald, amine, payload)
    return payload


def _build_explanation(pred, ald: str, amine: str, payload: dict) -> dict:
    """打分理由：优先 SHAP 归因（源码环境），缺失时回退树模型全局特征重要性。

    任何失败都返回 {"method": "none", "items": []}，绝不影响打分主流程。
    frozen 打包排除了 shap，桌面版自动走 global_importance 口径并如实标注。
    """
    route_reason = payload.get("tree_route") or ""
    empty = {"method": "none", "items": [], "route_reason": route_reason,
             "note": ""}
    try:
        tree, _info = pred.get_tree_for(ald, amine)
        if tree is None or getattr(tree, "model", None) is None:
            return empty
    except Exception:
        return empty

    # 1) SHAP 归因（models.attribution 依赖 shap，仅源码环境可用）
    try:
        from models.attribution import explain_pair_for_app
        exp = explain_pair_for_app(
            tree.model, tree.feature_cols, ald, amine,
            feature_flags=getattr(tree, "feature_flags", None) or {},
            te_rates=getattr(tree, "te_rates", None))
        items = []
        for rec in (exp.get("top_positive_features") or [])[:5]:
            items.append({
                "feature": rec["feature"],
                "label": rec.get("label_zh") or rec["feature"],
                "value": rec.get("value"),
                "weight": rec["shap"],
                "direction": "推高",
            })
        for rec in (exp.get("top_negative_features") or [])[:5]:
            items.append({
                "feature": rec["feature"],
                "label": rec.get("label_zh") or rec["feature"],
                "value": rec.get("value"),
                "weight": rec["shap"],
                "direction": "拉低",
            })
        dominant = exp.get("dominant_side")
        side_zh = {"aldehyde": "醛单体", "amine": "胺单体"}.get(dominant, "")
        return {
            "method": "shap",
            "items": items,
            "route_reason": route_reason,
            "dominant_side": side_zh,
            "note": "SHAP 归因：本次输入各特征对打分的推/拉贡献",
        }
    except Exception:
        pass

    # 2) 回退：树模型全局特征重要性（非本次归因，如实标注）
    try:
        imps = getattr(tree.model, "feature_importances_", None)
        cols = list(getattr(tree, "feature_cols", None) or [])
        if imps is None or not cols:
            return empty
        pairs = sorted(zip(cols, (float(v) for v in imps)),
                       key=lambda x: -x[1])[:8]
        try:
            from models.attribution import feature_label_zh
            labels = {c: feature_label_zh(c) for c, _ in pairs}
        except Exception:
            # frozen 无 models.attribution（依赖 shap，被打包排除）时的精简回退
            _ZH = (
                ("te_ald_film_rate", "醛单体历史成膜率先验"),
                ("te_amine_film_rate", "胺单体历史成膜率先验"),
                ("ald_n_aromatic_rings", "醛单体芳香环数"),
                ("amine_n_aromatic_rings", "胺单体芳香环数"),
                ("ald_mw", "醛单体分子量"),
                ("amine_mw", "胺单体分子量"),
                ("ald_", "醛单体特征"),
                ("amine_", "胺单体特征"),
            )

            def _zh(c: str) -> str:
                for prefix, label in _ZH:
                    if c == prefix:
                        return label
                for prefix, label in _ZH:
                    if c.startswith(prefix):
                        return f"{label}（{c}）"
                return c

            labels = {c: _zh(c) for c, _ in pairs}
        return {
            "method": "global_importance",
            "items": [{
                "feature": c,
                "label": labels.get(c, c),
                "weight": v,
                "direction": "",
            } for c, v in pairs],
            "route_reason": route_reason,
            "note": "桌面版未打包 SHAP，此处为模型全局重要特征（非本次输入的逐特征归因）",
        }
    except Exception:
        return empty


@router.get("/predict/history")
def predict_history(limit: int = 50):
    """查询历史：读 data/prediction_log.jsonl 的 prediction 记录，新→旧。

    记录含当时输入（ald/amine SMILES）与全部结果分量，供前端完整回显。
    """
    try:
        from utils.predict_log import LOG_PATH
    except Exception:
        from pathlib import Path
        LOG_PATH = Path("data/prediction_log.jsonl")
    limit = max(1, min(int(limit), 500))
    entries: list[dict] = []
    try:
        if LOG_PATH.is_file():
            import json as _json
            for line in LOG_PATH.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = _json.loads(line)
                except Exception:
                    continue
                if isinstance(rec, dict) and rec.get("type") == "prediction":
                    entries.append(rec)
    except Exception:
        entries = []
    entries.reverse()
    return {"history": entries[:limit], "count": len(entries)}


@router.post("/predict")
def predict(req: PredictRequest):
    ald, amine = req.ald_smiles.strip(), req.amine_smiles.strip()
    if not ald or not amine:
        raise HTTPException(400, "ald_smiles / amine_smiles 不能为空")
    try:
        return _predict_one(ald, amine, "api_single")
    except Exception as exc:
        raise HTTPException(500, f"预测失败：{type(exc).__name__}: {exc}")


@router.post("/predict/batch")
def predict_batch(req: BatchPredictRequest):
    if not req.pairs:
        raise HTTPException(400, "pairs 不能为空")
    out, errors = [], []
    for i, p in enumerate(req.pairs):
        try:
            out.append(_predict_one(p.ald_smiles.strip(),
                                    p.amine_smiles.strip(), "api_batch"))
        except Exception as exc:
            errors.append({"index": i, "error": f"{type(exc).__name__}: {exc}"})
    # 打分排序：有分者降序在前，无分（OOD=out 等）沉底
    out.sort(key=lambda r: (r["score"] is None, -(r["score"] or 0)))
    return {"results": out, "errors": errors}
