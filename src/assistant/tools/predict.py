"""predict_film 工具：走 src/predictor 现有预测（路由树 + GNN + OOD + 打分理由）。

复用 api.deps 的预测器单例与主分数口径（D29：max(树, GNN) 乐观召回），
保证助手报的分与页①打分完全一致。只读：不写预测日志、不回写收藏快照，
避免对话副作用污染用户数据。
"""

from __future__ import annotations


def _fmt_score(v) -> str:
    return f"{float(v):.3f}" if isinstance(v, (int, float)) else "（无）"


def predict_film(ald_smiles: str, amine_smiles: str) -> dict:
    """成膜打分。OOD=out 时分数置空并在 text 中显式警告。"""
    ald = (ald_smiles or "").strip()
    amine = (amine_smiles or "").strip()
    if not ald or not amine:
        return {"text": "参数缺失：ald_smiles 与 amine_smiles 均不能为空",
                "details": {}, "is_error": True}
    try:
        from api.deps import build_prediction_payload, get_predictor
        from api.routers.predict import _build_explanation

        pred = get_predictor()
        result = pred.predict(ald, amine)
        payload = build_prediction_payload(ald, amine, result,
                                           source="assistant")
        explanation = _build_explanation(pred, ald, amine, payload)
    except Exception as exc:
        return {"text": f"预测失败：{type(exc).__name__}: {exc}",
                "details": {}, "is_error": True}

    ood = payload.get("ood") or {}
    ood_level = ood.get("level", "none")
    lines: list[str] = []
    if ood_level == "out":
        lines.append(
            "⚠️ OOD 警告：该单体组超出模型训练分布，主分数已置空（不可信），"
            "以下分量仅供参考，请按分布外体系谨慎对待。")
    elif ood_level == "warn":
        lines.append("提示：该单体组接近训练分布边界（OOD=warn），置信度打折。")

    lines.append(f"主分数（max(树, GNN) 口径）：{_fmt_score(payload.get('score'))}")
    lines.append(
        f"树模型：{_fmt_score(payload.get('tree_score'))}"
        f"（±{_fmt_score(payload.get('tree_std'))}，"
        f"路由 {payload.get('tree_route') or '（无）'}，"
        f"模型 {payload.get('tree_model_name') or '（无）'}）")
    lines.append(f"GNN：{_fmt_score(payload.get('gnn_score'))}"
                 f"（±{_fmt_score(payload.get('gnn_std'))}）")

    items = (explanation or {}).get("items") or []
    if items:
        method_zh = {"shap": "SHAP 归因",
                     "global_importance": "树模型全局重要特征（非本次归因）"}.get(
            (explanation or {}).get("method"), "特征贡献")
        lines.append(f"打分理由（{method_zh}）：")
        for it in items[:6]:
            direction = it.get("direction") or ""
            weight = it.get("weight")
            w = f"{float(weight):+.4f}" if isinstance(weight, (int, float)) else ""
            lines.append(
                f"- {it.get('label') or it.get('feature')} {direction} {w}".strip())
        note = (explanation or {}).get("note")
        if note:
            lines.append(f"（{note}）")

    details = dict(payload)
    details["explanation"] = explanation
    return {"text": "\n".join(lines), "details": details, "is_error": False}
