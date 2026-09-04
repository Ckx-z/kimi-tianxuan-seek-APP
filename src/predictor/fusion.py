"""主分融合口径（v1.6.1 D31）：tree/GNN 分量 → 主分（保守修订）。

与 OOD 红线和组合级训练覆盖联动：
1. 低交联度红线（ood.checks.networkability.can_network=False）：
   score = min(0.25, 0.5 * min(tree, gnn))——单官能×单官能等化学上
   无法成网的组合最高 0.25，防止"苯甲醛+苯胺"式假高分；
2. 组合未见训练集（pair_seen=False）：GNN 分量 ×0.8 外推收缩；
3. 其余：max(tree, gnn)（保持 D29 口径，好组合分数不降——回归护栏）。
   两模型分歧 >0.25 时不改分数，仅在 score_flags.divergence 标注，
   由前端/日志提示（避免扰动既有排序与契约）。

api/deps.py 与 app/gradio_app.py 共用本模块（单一事实来源）。
"""

from __future__ import annotations

# 主分口径常量（随 payload 落库/落日志，供溯源与前端文案）
SCORE_POLICY = "max_tree_gnn_redline"
# 红线分数上限（低交联度组合）
REDLINE_CAP = 0.25
# 模型分歧阈值（超阈值仅标注 divergence flag，不改变分数）
DIVERGENCE_THRESHOLD = 0.25
# 组合未见训练集时 GNN 分量的外推收缩系数
GNN_UNSEEN_SHRINK = 0.8


def _clamp(v: float) -> float:
    return max(0.0, min(1.0, v))


def _effective_components(pred_result: dict) -> tuple[float | None, float | None, bool]:
    """取 tree/gnn 分量并应用外推收缩；返回 (tree, gnn_eff, can_network)。"""
    pred_result = pred_result or {}
    tree = pred_result.get("tree_probability")
    gnn = pred_result.get("gnn_probability")
    tree = tree if isinstance(tree, (int, float)) else None
    gnn = gnn if isinstance(gnn, (int, float)) else None
    ood = pred_result.get("ood") if isinstance(pred_result.get("ood"), dict) else {}
    checks = ood.get("checks") if isinstance(ood.get("checks"), dict) else {}
    nb = checks.get("networkability")
    nb = nb if isinstance(nb, dict) else {}
    # check_networkability 的 can_network 位于 details 内（顶层兼容兜底）
    nb_details = nb.get("details")
    nb_details = nb_details if isinstance(nb_details, dict) else {}
    can_network = bool(nb_details.get("can_network",
                                      nb.get("can_network", True)))
    # GNN 外推闸门：组合未见训练集 → 收缩
    if gnn is not None and not bool(pred_result.get("pair_seen", True)):
        gnn = gnn * GNN_UNSEEN_SHRINK
    return tree, gnn, can_network


def headline_score(pred_result: dict) -> tuple[float | None, str | None]:
    """主分（保守口径）。返回 (score, source) ∈ {"both","tree","gnn",None}。"""
    tree, gnn, can_network = _effective_components(pred_result)
    if tree is not None and gnn is not None:
        if not can_network:
            return _clamp(min(REDLINE_CAP, 0.5 * min(tree, gnn))), "both"
        return _clamp(max(tree, gnn)), "both"
    value = tree if tree is not None else gnn
    source = "tree" if tree is not None else ("gnn" if gnn is not None else None)
    if value is not None and not can_network:
        return _clamp(min(REDLINE_CAP, 0.5 * value)), source
    return (_clamp(value) if value is not None else None), source


def score_flags(pred_result: dict) -> dict:
    """主分决策标志（红/分歧/外推收缩），随 payload 透出供前端展示。"""
    pred_result = pred_result or {}
    _, _, can_network = _effective_components(pred_result)
    raw_tree = pred_result.get("tree_probability")
    raw_gnn = pred_result.get("gnn_probability")
    return {
        "redline": not can_network,
        "divergence": bool(isinstance(raw_tree, (int, float))
                           and isinstance(raw_gnn, (int, float))
                           and abs(raw_tree - raw_gnn) > DIVERGENCE_THRESHOLD),
        "gnn_pair_unseen": not bool(pred_result.get("pair_seen", True)),
    }
