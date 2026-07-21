"""阶段 14（真实回测）：当前路由模型 vs 旧 GNN 当年预测 vs 真实实验结果。

输入：
- reports/real_backtest_monomers.json（单体解析，MW 校验通过）
- data/experimental_refs/ 下两份 docx 提取的实验事实（手工整理于本文件 EXPERIMENTS）
- models/monomer_pool.json + tree_v4/tree_v4_noTE（路由双臂）

输出：reports/real_backtest_predictions.json（结构化预测中间结果，供分析脚本消费）

注意：
- 池内/池外判定做两套：canonical 化学判定（双方 canonicalize 后比较）与
  生产路由 exact-match（App 真实行为，MonomerPool 字符串精确匹配）。
- GNN v5.3 subprocess 慢则跳过（旧 GNN 当年预测已从实验记录提取，无需重跑）。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from rdkit import Chem  # noqa: E402

from predictor.routing import RoutedTreePredictor  # noqa: E402

MONOMERS_JSON = ROOT / "reports" / "real_backtest_monomers.json"
OUT = ROOT / "reports" / "real_backtest_predictions.json"
POOL_JSON = ROOT / "models" / "monomer_pool.json"

# ---------------------------------------------------------------------------
# 实验事实（从 data/experimental_refs/实验ABCDEF.docx 提取，逐条核对原文）
# outcome: fail=未形成有机械强度的膜 / partial=有膜但机械强度差（A5 相对最好）
# old_gnn_pred: 文档文本层记录的旧 GNN v5.3 预测；None=文档中未记录（D 系列重试）
# ---------------------------------------------------------------------------
DONE_EXPERIMENTS = [
    {"id": "A",  "node": "TAPB", "linker": "A6", "old_gnn_pred": 0.8194, "outcome": "fail",
     "note": "乙酸误加 18M 过量、加料顺序错"},
    {"id": "A1", "node": "TAPT", "linker": "A6", "old_gnn_pred": None, "outcome": "fail",
     "note": "两段加料；膜一碰就碎"},
    {"id": "A2", "node": "TAPB", "linker": "A6", "old_gnn_pred": 0.8194, "outcome": "fail",
     "note": "A 的重试（先自聚再加第二组分）"},
    {"id": "A5", "node": "TAPT", "linker": "A6", "old_gnn_pred": None, "outcome": "partial",
     "note": "与 A1 类似但相对最好（14 组中唯一稍好）"},
    {"id": "A8", "node": "TAPT", "linker": "A6", "old_gnn_pred": None, "outcome": "fail",
     "note": "v3.9 标准流程重试，仍未有效成膜"},
    {"id": "B",  "node": "TAPB", "linker": "A7", "old_gnn_pred": 0.809, "outcome": "fail",
     "note": "乙酸误加 18M 过量、加料顺序错"},
    {"id": "C",  "node": "TFPT", "linker": "H3", "old_gnn_pred": 0.040, "outcome": "fail",
     "note": "旧模型标注'预测不可靠，不适合非标准官能团'；H3 结构待确认"},
    {"id": "D",  "node": "TFPT", "linker": "B5", "old_gnn_pred": 0.596, "outcome": "fail",
     "note": "乙酸误加 18M、苯胺量与方案不符"},
    {"id": "D3", "node": "TFPT", "linker": "B5", "old_gnn_pred": None, "outcome": "fail",
     "note": "放大 1.5 倍+12M 乙酸；黑色贴壁膜逐渐消失"},
    {"id": "D4", "node": "TFPB", "linker": "B5", "old_gnn_pred": None, "outcome": "fail",
     "note": "开盖爆沸氯仿流失；未形成薄膜和沉淀"},
    {"id": "D7", "node": "TFPT", "linker": "B5", "old_gnn_pred": None, "outcome": "fail",
     "note": "粉末贴壁，非常易碎"},
    {"id": "D9", "node": "TFPT", "linker": "B5", "old_gnn_pred": None, "outcome": "fail",
     "note": "D 的 3 倍放大；薄膜碳化迹象"},
    {"id": "E",  "node": "TAPB", "linker": "A3", "old_gnn_pred": 0.589, "outcome": "fail",
     "note": "乙酸误加 18M 过量、加料顺序错"},
    {"id": "F",  "node": "TFPT", "linker": "B4", "old_gnn_pred": 0.486, "outcome": "fail",
     "note": "乙酸误加 18M 过量、加料顺序错"},
]

# 有旧 GNN 预测但未见实验结果记录的组合（计划/未记录；节点按标题 TAPB，
# G/H/I/J 正文写 TAPT，存在歧义——分布分析两种口径都算）
PLANNED_EXPERIMENTS = [
    {"id": "G", "node": "TAPB", "node_alt": "TAPT", "linker": "A5", "old_gnn_pred": 0.794},
    {"id": "H", "node": "TAPB", "node_alt": "TAPT", "linker": "A4", "old_gnn_pred": 0.803},
    {"id": "I", "node": "TAPB", "node_alt": "TAPT", "linker": "A1", "old_gnn_pred": 0.618},
    {"id": "J", "node": "TAPB", "node_alt": "TAPT", "linker": "A2", "old_gnn_pred": 0.643},
    {"id": "K", "node": "TFPT", "node_alt": None,   "linker": "B6", "old_gnn_pred": 0.645},
    {"id": "L", "node": "TFPT", "node_alt": None,   "linker": "B1", "old_gnn_pred": 0.784},
    {"id": "M", "node": "TFPT", "node_alt": None,   "linker": "B2", "old_gnn_pred": 0.771},
    {"id": "N", "node": "TFPT", "node_alt": None,   "linker": "B3", "old_gnn_pred": 0.627},
]

# 方案 v3.9 的 17 个组合（G5 的 H1–H4 待确认，运行时自动跳过）
SCHEME_17 = (
    [("G1-S1", "TAPT", "A5"), ("G1-S2", "TAPT", "A4"), ("G1-S3", "TAPT", "A6"), ("G1-S4", "TAPT", "A7")]
    + [("G2-S1", "TAPT", "A1"), ("G2-S2", "TAPT", "A2"), ("G2-S3", "TAPT", "A3")]
    + [("G3-S1", "TFPT", "B6"), ("G3-S2", "TFPT", "B5")]
    + [("G4-S1", "TFPT", "B1"), ("G4-S2", "TFPT", "B2"), ("G4-S3", "TFPT", "B3"), ("G4-S4", "TFPT", "B4")]
    + [("G5-S1", "TFPT", "H1"), ("G5-S2", "TFPT", "H2"), ("G5-S3", "TFPT", "H3"), ("G5-S4", "TFPT", "H4")]
)

ALDEHYDE_TYPES = {"aldehyde", "aldehyde_node"}


def canon(smi: str):
    m = Chem.MolFromSmiles(smi)
    return Chem.MolToSmiles(m) if m is not None else None


def main():
    monomers = json.load(open(MONOMERS_JSON, encoding="utf-8"))["monomers"]
    resolved = {k: v for k, v in monomers.items() if v["status"] == "resolved"}
    print(f"已解析单体 {len(resolved)} 个；待确认 {json.load(open(MONOMERS_JSON, encoding='utf-8'))['pending_confirmation']}")

    # canonical 池（化学判定）
    pool_raw = json.load(open(POOL_JSON, encoding="utf-8"))
    pool_canon = {"aldehydes": set(), "amines": set()}
    for s in pool_raw["aldehydes"]:
        c = canon(s)
        if c:
            pool_canon["aldehydes"].add(c)
    for s in pool_raw["amines"]:
        c = canon(s)
        if c:
            pool_canon["amines"].add(c)
    print(f"canonical 池：醛 {len(pool_canon['aldehydes'])} / 胺 {len(pool_canon['amines'])}")

    router = RoutedTreePredictor()
    router.load()

    # 打分理由（SHAP）——延迟加载，失败则降级
    explainer_ready = True
    try:
        from models.attribution import explain_pair_for_app
    except Exception as e:
        explainer_ready = False
        print(f"SHAP 归因不可用，降级跳过打分理由：{e}")

    def pair_of(node_id, linker_id):
        node = resolved.get(node_id)
        linker = resolved.get(linker_id)
        if node is None or linker is None:
            return None
        # 醛/胺角色：按官能团类型分配（酰肼按胺侧处理）
        if node["type"] in ALDEHYDE_TYPES:
            return node["canonical_smiles"], linker["canonical_smiles"]
        return linker["canonical_smiles"], node["canonical_smiles"]

    def predict_pair(node_id, linker_id):
        pair = pair_of(node_id, linker_id)
        if pair is None:
            missing = [x for x, v in ((node_id, resolved.get(node_id)), (linker_id, resolved.get(linker_id))) if v is None]
            return {"node": node_id, "linker": linker_id, "status": "skipped",
                    "reason": f"单体待确认: {missing}"}
        ald, amine = pair
        t0 = time.time()
        info = router.predict_with_info(ald, amine)
        # 双臂对照：生产路由臂之外，另一个臂也各跑一次
        p_pool = router.pool_model.predict_single(ald, amine)
        p_ext = router.extrap_model.predict_single(ald, amine)
        # canonical 化学判定
        ald_in = canon(ald) in pool_canon["aldehydes"]
        amine_in = canon(amine) in pool_canon["amines"]
        canon_route = ("in_pool" if ald_in and amine_in
                       else "both_unseen" if not ald_in and not amine_in
                       else ("ald_unseen" if not ald_in else "amine_unseen"))
        res = {
            "node": node_id, "linker": linker_id, "status": "ok",
            "ald_smiles": ald, "amine_smiles": amine,
            "tree_probability": info["probability"],
            "tree_model_name": info["model_name"],
            "route_production": info["route"],          # App 实际路由（exact-match）
            "route_canonical": canon_route,             # 化学判定（canonical 比对）
            "ald_seen_canonical": ald_in, "amine_seen_canonical": amine_in,
            "route_reason": info["route_reason"],
            "prob_tree_v4": p_pool, "prob_tree_v4_noTE": p_ext,
            "elapsed_s": round(time.time() - t0, 2),
        }
        if explainer_ready:
            model = router.extrap_model if info["route"] == "both_unseen" else router.pool_model
            try:
                exp = explain_pair_for_app(
                    model.model, model.feature_cols, ald, amine,
                    feature_flags=model.feature_flags, top_k=3,
                    te_rates=model.te_rates)
                res["top_positive"] = [
                    {"feature": r["feature"], "label_zh": r["label_zh"], "shap": round(r["shap"], 4)}
                    for r in exp["top_positive_features"][:3]]
                res["top_negative"] = [
                    {"feature": r["feature"], "label_zh": r["label_zh"], "shap": round(r["shap"], 4)}
                    for r in exp["top_negative_features"][:3]]
            except Exception as e:
                res["explain_error"] = str(e)
        return res

    out = {"done": [], "planned": [], "scheme17": []}
    for exp in DONE_EXPERIMENTS:
        r = predict_pair(exp["node"], exp["linker"])
        r.update({"id": exp["id"], "old_gnn_pred": exp["old_gnn_pred"],
                  "outcome": exp["outcome"], "note": exp["note"]})
        out["done"].append(r)
        print(f"[done] {exp['id']:3s} {exp['node']}+{exp['linker']:3s} -> {r.get('tree_probability')}", flush=True)

    for exp in PLANNED_EXPERIMENTS:
        r = predict_pair(exp["node"], exp["linker"])
        r.update({"id": exp["id"], "old_gnn_pred": exp["old_gnn_pred"], "outcome": "not_recorded"})
        if exp.get("node_alt"):
            alt = predict_pair(exp["node_alt"], exp["linker"])
            r["alt_node_prediction"] = {k: alt.get(k) for k in
                                        ("node", "tree_probability", "route_production", "route_canonical")}
        out["planned"].append(r)
        print(f"[plan] {exp['id']:3s} {exp['node']}+{exp['linker']:3s} -> {r.get('tree_probability')}", flush=True)

    for sid, node, linker in SCHEME_17:
        r = predict_pair(node, linker)
        r["id"] = sid
        out["scheme17"].append(r)
        print(f"[v3.9] {sid:6s} {node}+{linker:3s} -> {r.get('tree_probability', r.get('reason'))}", flush=True)

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"saved -> {OUT}")


if __name__ == "__main__":
    main()
