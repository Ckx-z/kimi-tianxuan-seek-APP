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
    return payload


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
