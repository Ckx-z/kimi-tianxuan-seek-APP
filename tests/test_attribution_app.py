"""前端「打分理由」（SHAP 归因）回归测试。

依赖 shap + rdkit + xgboost 与 models/tree_v3.pkl；
在缺少 shap 的环境（如 base 环境）中整文件跳过，不产生失败。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

shap = pytest.importorskip("shap", reason="当前环境无 shap，跳过打分理由测试")
rdkit = pytest.importorskip("rdkit", reason="当前环境无 rdkit，跳过打分理由测试")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from models.attribution import (  # noqa: E402
    GROUP_LABELS_ZH,
    classify_feature,
    explain_pair_for_app,
    feature_label_zh,
    format_explanation_zh,
    get_tree_explainer,
    group_contributions,
)

TP = "O=CC1=C(C=O)C(=O)C(C=O)=C1O"  # 经典醛单体
PA = "Nc1ccc(N)cc1"  # 经典胺单体

MODEL_PATH = PROJECT_ROOT / "models" / "tree_v3.pkl"


@pytest.fixture(scope="module")
def loaded_tree_v3():
    if not MODEL_PATH.exists():
        pytest.skip("models/tree_v3.pkl 不存在")
    import joblib

    data = joblib.load(MODEL_PATH)
    metrics = data.get("metrics") or {}
    flags = {
        k: metrics[k]
        for k in ("use_rules", "reduced_rules", "use_interaction",
                  "use_3d", "use_dimer", "n_confs")
        if k in metrics
    }
    return data["model"], data["feature_cols"], flags


class TestClassifyFeature:
    """分组归类：新增 split_3d 不得改变默认行为（向后兼容）。"""

    def test_default_behavior_unchanged(self):
        assert classify_feature("ald_3d_mol_volume") == "aldehyde"  # 旧行为：3D 并入醛组
        assert classify_feature("amine_3d_dipole_moment") == "amine"
        assert classify_feature("ald_mw") == "aldehyde"
        assert classify_feature("int_hadamard_tpsa") == "interaction"
        assert classify_feature("pair_site_ratio") == "interaction"
        assert classify_feature("rule_F_on_ald") == "rules"
        assert classify_feature("something_else") == "other"

    def test_split_3d(self):
        assert classify_feature("ald_3d_mol_volume", split_3d=True) == "aldehyde_3d"
        assert classify_feature("amine_3d_dipole_moment", split_3d=True) == "amine_3d"
        assert classify_feature("dimer_3d_mol_volume", split_3d=True) == "dimer_3d"
        assert classify_feature("ald_mw", split_3d=True) == "aldehyde"


class TestFeatureLabelZh:
    """特征名 → 中文标签映射。"""

    def test_monomer_labels(self):
        assert feature_label_zh("ald_mw") == "醛·分子量"
        assert feature_label_zh("amine_tpsa_per_site") == "胺·每位点极性表面积"

    def test_interaction_labels(self):
        assert "醛×胺" in feature_label_zh("int_hadamard_tpsa")
        assert "醛-胺差" in feature_label_zh("int_diff_logp")
        assert "醛/胺比" in feature_label_zh("int_ratio_mw")

    def test_rule_and_3d_labels(self):
        assert feature_label_zh("rule_C3邻位(禁)").startswith("规则·")
        assert feature_label_zh("rule_F_on_ald") == "规则·醛上含氟"
        assert feature_label_zh("ald_3d_mol_volume") == "醛 3D·分子体积"
        assert feature_label_zh("amine_3d_dipole_moment") == "胺 3D·偶极矩"

    def test_fallback_to_original_name(self):
        assert feature_label_zh("ald_unknown_metric") == "醛·unknown_metric"
        assert feature_label_zh("totally_unknown") == "totally_unknown"


class TestExplainPairForApp:
    """基于实际加载的 tree_v3 模型做端到端归因。"""

    def test_explain_and_format(self, loaded_tree_v3):
        model, feature_cols, flags = loaded_tree_v3
        exp = explain_pair_for_app(model, feature_cols, TP, PA, feature_flags=flags)

        # 预测分与分组贡献
        assert 0.0 <= exp["predicted_film_score"] <= 1.0
        groups = exp["group_contributions"]
        assert abs(sum(groups.values()) - 1.0) < 1e-6
        # tree_v3 含单体 3D 特征，3D 组应被拆出且非零
        assert groups.get("aldehyde_3d", 0) > 0
        assert groups.get("amine_3d", 0) > 0

        # Top± 特征带中文标签
        assert exp["top_positive_features"], "应有正向贡献特征"
        assert exp["top_negative_features"], "应有负向贡献特征"
        for rec in exp["top_positive_features"] + exp["top_negative_features"]:
            assert rec["label_zh"]
            assert rec["group_label_zh"] in GROUP_LABELS_ZH.values()

        # 格式化输出包含关键板块
        text = format_explanation_zh(exp, model_name="tree_v3")
        assert "打分理由" in text
        assert "tree_v3" in text
        assert "推高" in text and "拉低" in text
        assert "贡献强度占比" in text
        assert "主导贡献方" in text

    def test_explainer_is_cached(self, loaded_tree_v3):
        model, _, _ = loaded_tree_v3
        assert get_tree_explainer(model) is get_tree_explainer(model)

    def test_group_contributions_split_3d_sums_to_one(self, loaded_tree_v3):
        import numpy as np

        _, feature_cols, _ = loaded_tree_v3
        rng = np.random.default_rng(0)
        groups = group_contributions(rng.normal(size=len(feature_cols)),
                                     feature_cols, split_3d=True)
        assert abs(sum(groups.values()) - 1.0) < 1e-6


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
