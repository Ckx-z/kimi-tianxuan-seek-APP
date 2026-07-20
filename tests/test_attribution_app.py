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
    fill_te_values,
    format_explanation_zh,
    get_tree_explainer,
    group_contributions,
)

TP = "O=CC1=C(C=O)C(=O)C(C=O)=C1O"  # 经典醛单体
PA = "Nc1ccc(N)cc1"  # 经典胺单体
UNSEEN_ALD = "O=Cc1csc(C=O)c1"  # 不在 tree_v4 训练映射中的醛（噻吩二醛）
UNSEEN_AMINE = "Nc1ccnnc1"  # 不在 tree_v4 训练映射中的胺

MODEL_PATH = PROJECT_ROOT / "models" / "tree_v3.pkl"
MODEL_V4_PATH = PROJECT_ROOT / "models" / "tree_v4.pkl"


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


class TestTEFeatures:
    """tree_v4 TE 统计先验列的归类 / 中文标签 / 查表填充（单元级）。"""

    def test_classify_te_features_as_prior(self):
        assert classify_feature("te_ald_film_rate") == "prior"
        assert classify_feature("te_amine_film_rate") == "prior"
        assert classify_feature("te_ald_film_rate", split_3d=True) == "prior"
        # 不影响既有归类
        assert classify_feature("ald_mw") == "aldehyde"

    def test_te_labels_zh(self):
        assert feature_label_zh("te_ald_film_rate") == "先验·醛历史成膜率"
        assert feature_label_zh("te_amine_film_rate") == "先验·胺历史成膜率"

    def test_fill_te_values_known_unseen_and_none(self):
        rates = {"ald_rate": {"ALD1": 0.9}, "amine_rate": {"AM1": 0.2},
                 "global_mean": 0.5}
        cols = ["ald_mw", "te_ald_film_rate", "te_amine_film_rate"]
        # 已知醛 + 未见过胺 → 胺回退 global_mean
        out = fill_te_values(cols, "ALD1", "AM_NEW", rates)
        assert out == {"te_ald_film_rate": 0.9, "te_amine_film_rate": 0.5}
        # feature_cols 无 TE 列（v3）→ 空
        assert fill_te_values(["ald_mw"], "ALD1", "AM1", rates) == {}
        # te_rates=None（v3 旧 pkl）→ 空（保持补 0 旧行为）
        assert fill_te_values(cols, "ALD1", "AM1", None) == {}
        assert fill_te_values(cols, "ALD1", "AM1", {}) == {}


@pytest.fixture(scope="module")
def loaded_tree_v4():
    if not MODEL_V4_PATH.exists():
        pytest.skip("models/tree_v4.pkl 不存在")
    import joblib

    data = joblib.load(MODEL_V4_PATH)
    if not data.get("te_rates"):
        pytest.skip("models/tree_v4.pkl 无 te_rates（非 v4 格式）")
    metrics = data.get("metrics") or {}
    flags = {
        k: metrics[k]
        for k in ("use_rules", "reduced_rules", "use_interaction",
                  "use_3d", "use_dimer", "n_confs")
        if k in metrics
    }
    return data["model"], data["feature_cols"], flags, data["te_rates"]


class TestExplainTreeV4TE:
    """tree_v4（含 TE 列）的端到端归因：TE 列必须查 te_rates 填充而非补 0。"""

    def test_te_columns_filled_from_rates(self, loaded_tree_v4):
        _, feature_cols, _, te_rates = loaded_tree_v4
        vals = fill_te_values(feature_cols, TP, PA, te_rates)
        assert vals["te_ald_film_rate"] == te_rates["ald_rate"][TP]
        assert vals["te_amine_film_rate"] == te_rates["amine_rate"][PA]

    def test_unseen_monomer_falls_back_to_global_mean(self, loaded_tree_v4):
        _, feature_cols, _, te_rates = loaded_tree_v4
        assert UNSEEN_ALD not in te_rates["ald_rate"]
        assert UNSEEN_AMINE not in te_rates["amine_rate"]
        vals = fill_te_values(feature_cols, UNSEEN_ALD, UNSEEN_AMINE, te_rates)
        assert vals["te_ald_film_rate"] == te_rates["global_mean"]
        assert vals["te_amine_film_rate"] == te_rates["global_mean"]

    def test_prediction_matches_predictor_when_te_filled(self, loaded_tree_v4):
        """填 TE 后，归因路径的预测分必须与 TreeFilmPredictor 一致（旧补 0 行为失真）。"""
        from predictor.tree_model import TreeFilmPredictor

        model, feature_cols, flags, te_rates = loaded_tree_v4
        exp = explain_pair_for_app(model, feature_cols, TP, PA,
                                   feature_flags=flags, te_rates=te_rates)
        pred = TreeFilmPredictor(MODEL_V4_PATH).predict_single(TP, PA)
        assert exp["predicted_film_score"] == pytest.approx(pred, abs=1e-6)
        # 已知良好单体对得分应明显为正（旧补 0 行为曾给出 ~0.03）
        assert exp["predicted_film_score"] > 0.5

    def test_zero_fill_old_behavior_distorts_prediction(self, loaded_tree_v4):
        """回归锚点：对训练内单体，te_rates=None（旧行为）的预测与填 TE 不同。"""
        model, feature_cols, flags, te_rates = loaded_tree_v4
        with_te = explain_pair_for_app(model, feature_cols, TP, PA,
                                       feature_flags=flags, te_rates=te_rates)
        zero_fill = explain_pair_for_app(model, feature_cols, TP, PA,
                                         feature_flags=flags, te_rates=None)
        assert with_te["predicted_film_score_raw"] != pytest.approx(
            zero_fill["predicted_film_score_raw"], abs=1e-3)

    def test_prior_group_and_zh_labels_in_output(self, loaded_tree_v4):
        model, feature_cols, flags, te_rates = loaded_tree_v4
        exp = explain_pair_for_app(model, feature_cols, TP, PA,
                                   feature_flags=flags, te_rates=te_rates)
        groups = exp["group_contributions"]
        assert abs(sum(groups.values()) - 1.0) < 1e-6
        assert groups.get("prior", 0) > 0  # TE 先验组被单独统计
        assert exp["dominant_side"] in ("aldehyde", "amine")
        text = format_explanation_zh(exp, model_name="tree_v4")
        assert "打分理由" in text

    def test_v3_compatibility_unaffected(self, loaded_tree_v3):
        """v3（无 TE 列、无 te_rates）走旧路径，结果仍合法。"""
        model, feature_cols, flags = loaded_tree_v3
        exp = explain_pair_for_app(model, feature_cols, TP, PA,
                                   feature_flags=flags, te_rates=None)
        assert 0.0 <= exp["predicted_film_score"] <= 1.0
        assert exp["group_contributions"].get("prior", 0.0) == 0.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
