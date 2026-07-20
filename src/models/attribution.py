"""COF 成膜预测的多层级归因模块。

提供：
1. 全局特征重要性（SHAP / XGBoost gain）
2. 样本级分组归因：醛 / 胺 / 交互 / 规则
3. 官能团级归因：基于 SMARTS 检测的局部结构贡献
4. 可读的推荐解释
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import shap
from rdkit import Chem

import sys
from pathlib import Path
SRC_DIR = Path(__file__).resolve().parent.parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


# 官能团 SMARTS 模式（用于可读解释）
FUNCTIONAL_GROUPS = {
    # 醛侧
    "醛基": {"smarts": "[CX3H1](=O)[#6]", "side": "aldehyde"},
    "醛邻位 F": {"smarts": "[F][c]cc[C](=O)", "side": "aldehyde"},
    "醛邻位 CF3": {"smarts": "[CX4](F)(F)(F)[c]cc[C](=O)", "side": "aldehyde"},
    # 胺侧
    "伯胺": {"smarts": "[NH2][c]", "side": "amine"},
    "胺邻位 F": {"smarts": "[F][c]cc[NH2]", "side": "amine"},
    # 通用
    "苯环": {"smarts": "c1ccccc1", "side": "both"},
    "炔基": {"smarts": "[C]#[C]", "side": "both"},
    "杂芳环": {"smarts": "[#7,#8,#16;r]", "side": "both"},
    "三氟甲基": {"smarts": "[CX4](F)(F)(F)", "side": "both"},
    "芳香 F": {"smarts": "[F][c]", "side": "both"},
}


def classify_feature(feature_name: str, split_3d: bool = False) -> str:
    """把特征名归类到醛/胺/交互/规则。

    split_3d=True 时把 3D 描述符从单体特征中拆出，单独归为
    aldehyde_3d / amine_3d / dimer_3d（与归因报告的口径一致）；
    默认 False 保持历史行为（3D 特征并入醛/胺组），向后兼容。
    """
    if split_3d:
        if feature_name.startswith("ald_3d_"):
            return "aldehyde_3d"
        elif feature_name.startswith("amine_3d_"):
            return "amine_3d"
        elif feature_name.startswith("dimer_3d_"):
            return "dimer_3d"
    if feature_name.startswith("ald_"):
        return "aldehyde"
    elif feature_name.startswith("amine_"):
        return "amine"
    elif feature_name.startswith("int_") or feature_name.startswith("pair_"):
        return "interaction"
    elif feature_name.startswith("rule_"):
        return "rules"
    return "other"


def detect_functional_groups(ald_smiles: str, amine_smiles: str) -> Dict[str, Dict[str, bool]]:
    """检测单体中存在的官能团。"""
    result = {"aldehyde": {}, "amine": {}}
    mols = {"aldehyde": Chem.MolFromSmiles(ald_smiles),
            "amine": Chem.MolFromSmiles(amine_smiles)}

    for name, info in FUNCTIONAL_GROUPS.items():
        patt = Chem.MolFromSmarts(info["smarts"])
        if patt is None:
            continue
        side = info["side"]
        for mol_side, mol in mols.items():
            if mol is None:
                continue
            if side == "both" or side == mol_side:
                result[mol_side][name] = bool(mol.HasSubstructMatch(patt))
    return result


def global_shap_summary(model, X: pd.DataFrame, feature_cols: List[str],
                        max_samples: int = 500) -> pd.DataFrame:
    """全局 SHAP 特征重要性。"""
    explainer = shap.TreeExplainer(model)
    X_sub = X[feature_cols].sample(n=min(max_samples, len(X)), random_state=42) if len(X) > max_samples else X[feature_cols]
    shap_values = explainer.shap_values(X_sub)

    mean_abs = np.abs(np.array(shap_values)).mean(axis=0)
    return pd.DataFrame({
        "feature": feature_cols,
        "mean_abs_shap": mean_abs,
        "group": [classify_feature(f) for f in feature_cols],
    }).sort_values("mean_abs_shap", ascending=False)


def group_contributions(shap_values: np.ndarray, feature_cols: List[str],
                        split_3d: bool = False) -> Dict[str, float]:
    """把 SHAP 值按醛/胺/交互/规则/3D 分组求和（取绝对值，表示贡献强度）。

    split_3d=True 时 3D 描述符单独成组（aldehyde_3d / amine_3d / dimer_3d）。
    """
    groups = {"aldehyde": 0.0, "amine": 0.0, "interaction": 0.0, "rules": 0.0,
              "aldehyde_3d": 0.0, "amine_3d": 0.0, "dimer_3d": 0.0, "other": 0.0}
    for val, feat in zip(shap_values, feature_cols):
        groups[classify_feature(feat, split_3d=split_3d)] += abs(float(val))
    total = sum(groups.values())
    if total > 0:
        groups = {k: v / total for k, v in groups.items()}
    return groups


def explain_single(model, feature_cols: List[str], ald_smiles: str, amine_smiles: str,
                   use_3d: bool = False, use_dimer: bool = False, n_confs: int = 5) -> Dict:
    """对单个醛-胺组合生成可解释报告。

    use_3d / use_dimer / n_confs 必须与模型训练时的特征开关一致，
    否则 3D 特征会缺省为 0，导致归因失真。
    """
    from features.descriptors import compute_pair_features

    feats = compute_pair_features(ald_smiles, amine_smiles,
                                   use_rules=True, reduced_rules=True, use_interaction=True,
                                   use_3d=use_3d, use_dimer=use_dimer, n_confs=n_confs)
    X = pd.DataFrame([{k: feats.get(k, 0.0) for k in feature_cols}])

    pred = float(model.predict(X)[0])

    explainer = shap.TreeExplainer(model)
    shap_values = explainer.shap_values(X)[0]

    # 分组贡献
    groups = group_contributions(shap_values, feature_cols)

    # Top 特征
    feature_df = pd.DataFrame({
        "feature": feature_cols,
        "shap": shap_values,
        "value": X.iloc[0].values,
        "group": [classify_feature(f) for f in feature_cols],
    })
    top_positive = feature_df[feature_df["shap"] > 0].sort_values("shap", ascending=False).head(5)
    top_negative = feature_df[feature_df["shap"] < 0].sort_values("shap").head(5)

    # 官能团检测
    fg = detect_functional_groups(ald_smiles, amine_smiles)
    dominant_side = max(groups, key=lambda k: groups[k] if k in ("aldehyde", "amine") else -1)
    dominant_fgs = [name for name, present in fg.get(dominant_side, {}).items() if present]

    return {
        "ald_smiles": ald_smiles,
        "amine_smiles": amine_smiles,
        "predicted_film_score": pred,
        "group_contributions": groups,
        "dominant_side": dominant_side,
        "dominant_functional_groups": dominant_fgs,
        "top_positive_features": top_positive[["feature", "shap", "value", "group"]].to_dict("records"),
        "top_negative_features": top_negative[["feature", "shap", "value", "group"]].to_dict("records"),
    }


def summarize_pair_interaction(ald_smiles: str, amine_smiles: str,
                                model, feature_cols: List[str],
                                use_3d: bool = False, use_dimer: bool = False,
                                n_confs: int = 5) -> str:
    """生成一段人类可读的归因摘要。"""
    exp = explain_single(model, feature_cols, ald_smiles, amine_smiles,
                         use_3d=use_3d, use_dimer=use_dimer, n_confs=n_confs)
    lines = [
        f"预测成膜得分: {exp['predicted_film_score']:.3f}",
        f"主导贡献方: {exp['dominant_side']}（醛/胺）",
        f"该单体上的关键官能团: {', '.join(exp['dominant_functional_groups']) or '无特殊官能团'}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 前端「打分理由」辅助：中文标签映射 + explainer 缓存 + 应用级归因 + 格式化
# ---------------------------------------------------------------------------

# 分组名 → 中文标签
GROUP_LABELS_ZH = {
    "aldehyde": "醛单体结构",
    "amine": "胺单体结构",
    "interaction": "醛-胺交互匹配",
    "rules": "化学规则",
    "aldehyde_3d": "醛 3D 结构",
    "amine_3d": "胺 3D 结构",
    "dimer_3d": "二聚体 3D 结构",
    "other": "其他特征",
}

# 单体基础描述符（ald_/amine_ 前缀后的公共部分）
_BASE_FEATURE_LABELS_ZH = {
    "mw": "分子量",
    "n_atoms": "原子数",
    "n_heavy": "重原子数",
    "n_rotatable": "可旋转键数",
    "n_aromatic_rings": "芳香环数",
    "n_rings": "环数",
    "tpsa": "极性表面积(TPSA)",
    "logp": "脂溶性(LogP)",
    "n_reactive_sites": "反应位点数",
    "has_acetylene": "含炔基",
    "n_acetylene": "炔基数量",
    "aromatic_frac": "芳香原子占比",
    "ring_frac": "环原子占比",
    "is_symmetric": "结构对称性",
    "has_heterocycle": "含杂环",
    "aromatic_ring_count": "芳香环计数",
    "mw_per_site": "每位点分子量",
    "n_atoms_per_site": "每位点原子数",
    "n_heavy_per_site": "每位点重原子数",
    "n_aromatic_rings_per_site": "每位点芳香环数",
    "n_rings_per_site": "每位点环数",
    "tpsa_per_site": "每位点极性表面积",
}

# 3D 描述符（ald_3d_/amine_3d_/dimer_3d_ 前缀后的公共部分）
_3D_FEATURE_LABELS_ZH = {
    "pmi_i1_i3": "主惯性轴比 I1/I3（延展度）",
    "pmi_i2_i3": "主惯性轴比 I2/I3（不对称度）",
    "mol_volume": "分子体积",
    "radius_ratio": "最大/最小半径比（伸展程度）",
    "sp3_ratio": "sp3 碳占比",
    "radius_gyration": "回转半径",
    "num_rotatable_bonds": "可旋转键数",
    "aromatic_planarity_rmsd": "芳香平面度 RMSD",
    "h_shielding": "氢屏蔽度",
    "dipole_moment": "偶极矩",
    "planar_rmsd": "平面度 RMSD",
    "min_site_dist": "最近反应位点距离",
    "n_close_sites": "近距反应位点对数",
    "inter_plane_angle": "层间夹角",
}

# 化学规则（rule_ 前缀后的部分；规则名本身多为中文）
_RULE_FEATURE_LABELS_ZH = {
    "无有效醛基": "无有效醛基",
    "无有效伯胺": "无有效伯胺",
    "醛基数不匹配": "醛基数不匹配",
    "胺基数不匹配": "胺基数不匹配",
    "官能团非中心对称": "官能团非中心对称",
    "非对位(间位)": "非对位取代（间位）",
    "非对位(邻位)": "非对位取代（邻位）",
    "C3邻位(禁)": "C3 邻位（禁忌）",
    "C3对位(非间位)": "C3 对位（非间位）",
    "F_on_ald": "醛上含氟",
    "F_ortho_ald": "醛邻位氟",
    "F_on_amine": "胺上含氟",
    "CF3_on_ald": "醛上含三氟甲基",
    "CF3_on_amine": "胺上含三氟甲基",
    "CF3_ortho_ald": "醛邻位三氟甲基",
    "polyfluoro_ald": "醛多氟取代",
    "ald_e_withdrawing": "醛侧吸电子基",
    "am_e_withdrawing": "胺侧吸电子基",
    "e_favorable": "电子效应有利",
    "ortho_steric_ald": "醛邻位位阻",
}

# 配对差异/比例特征（pair_ 前缀后的部分）
_PAIR_FEATURE_LABELS_ZH = {
    "site_ratio": "醛/胺反应位点比",
    "mw_ratio": "醛/胺分子量比",
    "ring_ratio": "醛/胺环数比",
    "aromatic_ring_ratio": "醛/胺芳香环数比",
    "rotatable_diff": "醛-胺柔性差异",
    "logp_diff": "醛-胺脂溶性差异",
    "tpsa_diff": "醛-胺极性差异",
}


def feature_label_zh(feature_name: str) -> str:
    """把特征名翻译成普通人看得懂的中文标签，映射不到时回退为原名。"""
    if feature_name.startswith("rule_"):
        base = feature_name[5:]
        return f"规则·{_RULE_FEATURE_LABELS_ZH.get(base, base)}"
    for prefix, side_zh in (("ald_3d_", "醛 3D·"), ("amine_3d_", "胺 3D·"),
                            ("dimer_3d_", "二聚体 3D·")):
        if feature_name.startswith(prefix):
            base = feature_name[len(prefix):]
            return side_zh + _3D_FEATURE_LABELS_ZH.get(base, base)
    if feature_name.startswith("int_"):
        rest = feature_name[4:]
        for op, op_zh in (("hadamard_", "醛×胺·"), ("diff_", "醛-胺差·"),
                          ("ratio_", "醛/胺比·")):
            if rest.startswith(op):
                base = rest[len(op):]
                return op_zh + _BASE_FEATURE_LABELS_ZH.get(base, base)
        return feature_name
    if feature_name.startswith("ald_"):
        return "醛·" + _BASE_FEATURE_LABELS_ZH.get(feature_name[4:], feature_name[4:])
    if feature_name.startswith("amine_"):
        return "胺·" + _BASE_FEATURE_LABELS_ZH.get(feature_name[6:], feature_name[6:])
    if feature_name.startswith("pair_"):
        return "配对·" + _PAIR_FEATURE_LABELS_ZH.get(feature_name[5:], feature_name[5:])
    return feature_name


# 与 explain_single 中硬编码的默认特征开关一致；实际值由模型 pkl 的 metrics 覆盖
_DEFAULT_FEATURE_FLAGS = {
    "use_rules": True,
    "reduced_rules": True,
    "use_interaction": True,
    "use_3d": False,
    "use_dimer": False,
    "n_confs": 5,
}

# TreeExplainer 缓存：按模型对象缓存，避免每次预测重建（单次可省约 0.5-1s）
_EXPLAINER_CACHE: Dict[int, "shap.TreeExplainer"] = {}


def get_tree_explainer(model) -> "shap.TreeExplainer":
    """获取（并缓存）某个树模型的 SHAP TreeExplainer。"""
    key = id(model)
    if key not in _EXPLAINER_CACHE:
        _EXPLAINER_CACHE[key] = shap.TreeExplainer(model)
    return _EXPLAINER_CACHE[key]


def explain_pair_for_app(model, feature_cols: List[str], ald_smiles: str, amine_smiles: str,
                         feature_flags: Optional[Dict] = None, top_k: int = 5) -> Dict:
    """基于已加载的树模型，对单个醛-胺组合做 SHAP 归因（供 App「打分理由」使用）。

    Args:
        model: 已加载的树模型（如 TreeFilmPredictor.model），由调用方动态传入
        feature_cols: 模型训练时的特征列（TreeFilmPredictor.feature_cols）
        ald_smiles / amine_smiles: 单体 SMILES
        feature_flags: 特征开关（TreeFilmPredictor.feature_flags，来自 pkl 内 metrics）；
            缺省时退回 explain_single 的默认开关
        top_k: 正/负向各展示的 Top 特征数

    Returns:
        含预测分、分组贡献（3D 单独成组）、Top± 特征（带中文标签）、主导方的字典
    """
    from features.descriptors import compute_pair_features

    flags = dict(_DEFAULT_FEATURE_FLAGS)
    if feature_flags:
        flags.update({k: v for k, v in feature_flags.items() if k in flags})

    feats = compute_pair_features(ald_smiles, amine_smiles, **flags)
    X = pd.DataFrame([{k: feats.get(k, 0.0) for k in feature_cols}])

    pred_raw = float(model.predict(X)[0])

    explainer = get_tree_explainer(model)
    shap_values = explainer.shap_values(X)[0]

    groups = group_contributions(shap_values, feature_cols, split_3d=True)

    feature_df = pd.DataFrame({
        "feature": feature_cols,
        "shap": shap_values,
        "value": X.iloc[0].values,
        "group": [classify_feature(f, split_3d=True) for f in feature_cols],
    })
    feature_df["label_zh"] = feature_df["feature"].map(feature_label_zh)
    feature_df["group_label_zh"] = feature_df["group"].map(GROUP_LABELS_ZH)

    cols = ["feature", "label_zh", "shap", "value", "group", "group_label_zh"]
    top_positive = (feature_df[feature_df["shap"] > 0]
                    .sort_values("shap", ascending=False).head(top_k))
    top_negative = (feature_df[feature_df["shap"] < 0]
                    .sort_values("shap").head(top_k))

    # 主导贡献方：醛/胺两侧合并各自 3D 后比较
    side_strength = {
        "aldehyde": groups.get("aldehyde", 0.0) + groups.get("aldehyde_3d", 0.0),
        "amine": groups.get("amine", 0.0) + groups.get("amine_3d", 0.0),
    }
    dominant_side = max(side_strength, key=side_strength.get)
    fg = detect_functional_groups(ald_smiles, amine_smiles)
    dominant_fgs = [name for name, present in fg.get(dominant_side, {}).items() if present]

    return {
        "ald_smiles": ald_smiles,
        "amine_smiles": amine_smiles,
        "predicted_film_score": min(max(pred_raw, 0.0), 1.0),
        "predicted_film_score_raw": pred_raw,
        "group_contributions": groups,
        "dominant_side": dominant_side,
        "dominant_functional_groups": dominant_fgs,
        "top_positive_features": top_positive[cols].to_dict("records"),
        "top_negative_features": top_negative[cols].to_dict("records"),
    }


def _fmt_feature_value(feature_name: str, value: float) -> str:
    """把特征取值格式化为易读形式（二元特征显示为 是/否，连续特征显示数值）。"""
    v = float(value)
    is_binary = (
        feature_name.startswith("rule_")
        or feature_name.startswith(("ald_has_", "amine_has_", "ald_is_", "amine_is_"))
    )
    if is_binary and v in (0.0, 1.0):
        return "是" if v == 1.0 else "否"
    av = abs(v)
    if av >= 1000:
        return f"{v:.0f}"
    if av >= 10:
        return f"{v:.1f}"
    return f"{v:.3f}"


def format_explanation_zh(exp: Dict, model_name: str = "") -> str:
    """把 explain_pair_for_app 的结果格式化为前端中文 Markdown。

    说明「哪个官能团/特征推高或拉低了成膜分」：SHAP 正值 = 推高，负值 = 拉低。
    """
    lines = ["### 打分理由（SHAP 归因）", ""]
    if model_name:
        lines.append(
            f"基于树模型 **{model_name}** 的 SHAP 分析：正值表示该特征**推高**成膜分，"
            "负值表示**拉低**成膜分（相对于训练样本的平均水平）。")
    else:
        lines.append("SHAP 正值表示该特征**推高**成膜分，负值表示**拉低**成膜分。")
    lines.append("")

    # 分组贡献强度占比（只展示非零组）
    groups = exp.get("group_contributions", {})
    nonzero = [(g, v) for g, v in groups.items() if v > 0.001]
    nonzero.sort(key=lambda kv: kv[1], reverse=True)
    if nonzero:
        lines.append("**各组贡献强度占比**（对该组打分的相对影响）：")
        lines.append("")
        for g, v in nonzero:
            bar = "█" * max(1, round(v * 20))
            lines.append(f"- {GROUP_LABELS_ZH.get(g, g)}：{bar} {v * 100:.1f}%")
        lines.append("")

    def _top_section(title: str, records: List[Dict], sign_zh: str) -> None:
        lines.append(f"**{title} Top {len(records)}**")
        lines.append("")
        if not records:
            lines.append("- （无）")
        for i, rec in enumerate(records, 1):
            lines.append(
                f"{i}. {rec['label_zh']}（`{rec['feature']}`，"
                f"取值 {_fmt_feature_value(rec['feature'], rec['value'])}）："
                f"{rec['shap']:+.3f} {sign_zh}")
        lines.append("")

    _top_section("✅ 推高成膜分的特征", exp.get("top_positive_features", []), "推高")
    _top_section("⚠️ 拉低成膜分的特征", exp.get("top_negative_features", []), "拉低")

    side_zh = "醛侧单体" if exp.get("dominant_side") == "aldehyde" else "胺侧单体"
    fgs = exp.get("dominant_functional_groups") or []
    lines.append(f"**主导贡献方**：{side_zh}"
                 + (f"；该侧关键官能团：{ '、'.join(fgs) }" if fgs else "；该侧无特殊官能团"))
    return "\n".join(lines)


if __name__ == "__main__":
    # 简单测试
    import json
    import joblib

    model_path = "models/tree_v2.pkl"
    data = joblib.load(model_path)
    model = data["model"]
    feature_cols = data["feature_cols"]

    ald = "O=CC1=C(C=O)C(=O)C(C=O)=C1O"
    amine = "Nc1ccc(N)cc1"
    exp = explain_single(model, feature_cols, ald, amine)
    print(json.dumps(exp, indent=2, ensure_ascii=False))
