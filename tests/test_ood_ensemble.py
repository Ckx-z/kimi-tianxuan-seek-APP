"""P2（D27）单元测试：OOD 三级检测 + bagging 集成不确定度 + App 冒烟。

覆盖：
- OOD 三类触发：
  a. 官能团适配性：酰肼案例（TFPT+H3 类）→ out；TAPT+A2 → none
  b. 单体新颖性：虚构双未见大芳香醛 → warning
  c. 特征区域漂移：超包络 → warning
- 集成加载与 std 输出：tree_v4_ens / tree_v4_noTE_ens 预测 mean ± std（std > 0）
- 向后兼容：单模型 pkl predict/predict_single 不变、std 为 0
- App 回调冒烟：Tp+Pa（none）/ 含氟双未见（warning）/ 酰肼（out，不出分数）

依赖真实模型与包络文件，缺失时整文件跳过。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from predictor import FilmPredictor  # noqa: E402
from predictor.ood import (  # noqa: E402
    LEVEL_NONE,
    LEVEL_OUT,
    LEVEL_WARNING,
    check_feature_drift,
    check_functional_groups,
    check_novelty,
    check_ood,
    load_envelope,
)
from predictor.routing import EXTRAP_MODEL_PATH, POOL_MODEL_PATH, MonomerPool  # noqa: E402
from predictor.tree_model import TreeFilmPredictor  # noqa: E402

TP = "O=CC1=C(C=O)C(=O)C(C=O)=C1O"  # Tp（池内）
PA = "Nc1ccc(N)cc1"  # Pa（池内）
TAPT = "Nc1ccc(-c2nc(-c3ccc(N)cc3)nc(-c3ccc(N)cc3)n2)cc1"
A2 = "O=Cc1c(F)c(F)c(C=O)c(F)c1F"  # 四氟对苯二甲醛（池内）
TFPT = "O=Cc1ccc(-c2nc(-c3ccc(C=O)cc3)nc(-c3ccc(C=O)cc3)n2)cc1"
H3 = ("NNC(=O)c1cc(OCC(F)(F)C(F)(F)C(F)(F)C(F)(F)F)c(C(=O)NN)"
      "cc1OCC(F)(F)C(F)(F)C(F)(F)C(F)(F)F")  # 全氟链酰肼（exp_011 C 组案例）
BIG_ALD = "O=Cc1ccc(-c2ccc(-c3ccc(-c4ccc(-c5ccc(C=O)cc5)cc4)cc3)cc2)cc1"  # 虚构大芳香醛
BIG_AM = "Nc1ccc(-c2ccc(-c3ccc(-c4ccc(-c5ccc(N)cc5)cc4)cc3)cc2)cc1"  # 虚构大芳香胺
UNSEEN_ALD = "O=Cc1csc(C=O)c1"
UNSEEN_AMINE = "Nc1ccnnc1"

_ASSETS = (POOL_MODEL_PATH, EXTRAP_MODEL_PATH,
           PROJECT_ROOT / "models" / "monomer_pool.json",
           PROJECT_ROOT / "models" / "feature_envelope.json")

pytestmark = pytest.mark.skipif(any(not p.exists() for p in _ASSETS),
                                reason="集成模型/单体池/特征包络文件不存在")


@pytest.fixture(scope="module")
def pool():
    return MonomerPool.load()


class TestFunctionalGroupOOD:
    """a 类：官能团适配性。"""

    def test_hydrazide_is_out(self):
        """酰肼案例（C 组 TFPT+H3 类 SMILES）→ out，原因含非标准官能团。"""
        r = check_functional_groups(TFPT, H3)
        assert r["level"] == LEVEL_OUT
        assert any("酰肼" in x or "非标准官能团" in x for x in r["reasons"])

    def test_standard_pair_is_none(self):
        """TAPT+A2（标准醛-胺）→ none。"""
        r = check_functional_groups(A2, TAPT)
        assert r["level"] == LEVEL_NONE
        assert r["reasons"] == []

    def test_no_amine_group_is_out(self):
        """胺侧无伯胺/仲胺 → out。"""
        r = check_functional_groups(TP, "c1ccncc1")  # 吡啶，无游离胺
        assert r["level"] == LEVEL_OUT
        assert any("伯胺" in x for x in r["reasons"])

    def test_no_aldehyde_group_is_out(self):
        """醛侧无醛基 → out。"""
        r = check_functional_groups("c1ccccc1", PA)
        assert r["level"] == LEVEL_OUT
        assert any("醛基" in x for x in r["reasons"])


class TestNoveltyOOD:
    """b 类：单体新颖性（与路由臂联动）。"""

    def test_both_unseen_is_warning(self, pool):
        r = check_novelty(UNSEEN_ALD, UNSEEN_AMINE, pool)
        assert r["level"] == LEVEL_WARNING
        assert any("双未见" in x and "外推" in x for x in r["reasons"])

    def test_in_pool_is_none(self, pool):
        assert check_novelty(TP, PA, pool)["level"] == LEVEL_NONE

    def test_single_unseen_is_none(self, pool):
        """一新一熟不触发新颖性 warning（routed_strict 仍走池内臂）。"""
        assert check_novelty(UNSEEN_ALD, PA, pool)["level"] == LEVEL_NONE


class TestFeatureDriftOOD:
    """c 类：特征区域漂移（fold3 诊断）。"""

    def test_envelope_self_describing(self):
        env = load_envelope()
        assert env is not None
        assert "ald_mw" in env["features"] and "p05" in env["features"]["ald_mw"]

    def test_giant_aromatic_is_warning(self):
        """虚构双未见大芳香醛/胺组合：超包络比例 > 10% → warning。"""
        r = check_feature_drift(BIG_ALD, BIG_AM)
        assert r["level"] == LEVEL_WARNING
        assert r["details"]["out_ratio"] > r["details"]["threshold"]

    def test_normal_pair_no_drift(self):
        assert check_feature_drift(TP, PA)["level"] == LEVEL_NONE


class TestOODCombined:
    """三类合并：等级取最高。"""

    def test_hydrazide_combined_out(self, pool):
        r = check_ood(TFPT, H3, pool=pool)
        assert r["level"] == LEVEL_OUT
        assert any("酰肼" in x for x in r["reasons"])

    def test_tapt_a2_none(self, pool):
        assert check_ood(A2, TAPT, pool=pool)["level"] == LEVEL_NONE

    def test_giant_unseen_warning(self, pool):
        r = check_ood(BIG_ALD, BIG_AM, pool=pool)
        assert r["level"] == LEVEL_WARNING  # 新颖性 + 漂移均为 warning，不升级为 out


@pytest.fixture(scope="module")
def ens_predictor():
    return FilmPredictor(use_gnn=False, use_tree=True)


class TestEnsemble:
    """bagging 集成：mean ± std 输出 + 向后兼容。"""

    def test_ensemble_loaded(self):
        t = TreeFilmPredictor(POOL_MODEL_PATH)
        t.load()
        assert t.ensemble is not None and len(t.ensemble) == 5
        assert t.model is t.ensemble[0]  # 归因等单模型消费方兼容

    def test_ensemble_std_positive(self, ens_predictor):
        r = ens_predictor.predict(TP, PA)
        assert r["tree_model_name"] == "tree_v4_ens"
        assert 0.0 <= r["tree_probability"] <= 1.0
        assert r["score_std"] > 0  # bagging 成员分歧（认知不确定度）
        assert r["tree_std"] == r["score_std"]

    def test_ood_fields_present(self, ens_predictor):
        r = ens_predictor.predict(TP, PA)
        assert r["ood"]["level"] == LEVEL_NONE
        r2 = ens_predictor.predict(TFPT, H3)
        assert r2["ood"]["level"] == LEVEL_OUT

    def test_backward_compat_single_model(self):
        """单模型 pkl：predict/predict_single 行为不变，std 为 0。"""
        v3 = PROJECT_ROOT / "models" / "tree_v3.pkl"
        if not v3.exists():
            pytest.skip("tree_v3.pkl 不存在")
        t = TreeFilmPredictor(v3)
        t.load()
        assert t.ensemble is None
        prob = t.predict_single(TP, PA)
        mean, std = t.predict_single_with_std(TP, PA)
        assert prob == pytest.approx(mean)
        assert std == 0.0


class TestAppSmoke:
    """端到端冒烟：App 回调三种 OOD 情形（monkeypatch 免 GNN 保快速确定）。"""

    @pytest.fixture(scope="class")
    def app_module(self, request):
        sys.path.insert(0, str(PROJECT_ROOT / "app"))
        import gradio_app
        # 测试环境免 GNN subprocess：固定为树模型路由预测器
        fast = FilmPredictor(use_gnn=False, use_tree=True)
        original = gradio_app._get_predictor
        gradio_app._get_predictor = lambda: fast
        request.addfinalizer(lambda: setattr(gradio_app, "_get_predictor", original))
        return gradio_app

    def test_in_pool_none(self, app_module):
        prob_text, _, _, explain_text, *_ = app_module.predict(TP, PA)
        assert "成膜打分（倾向性）" in prob_text
        assert "⛔" not in prob_text and "⚠️" not in prob_text
        assert "±" in prob_text  # 不确定度展示
        assert "打分理由" in explain_text

    def test_unseen_warning(self, app_module):
        prob_text, _, _, explain_text, *_ = app_module.predict(BIG_ALD, BIG_AM)
        assert "⚠️" in prob_text and "⛔" not in prob_text
        assert "外推模式" in prob_text or "超出训练分布" in prob_text
        assert "打分理由" in explain_text  # warning 仍给分数与理由

    def test_hydrazide_out(self, app_module):
        prob_text, _, _, explain_text, *_ = app_module.predict(TFPT, H3)
        assert "⛔" in prob_text and "模型不适用" in prob_text
        assert "非标准官能团" in prob_text or "酰肼" in prob_text
        # out 不出分数
        assert "树模型 (" not in prob_text and "综合打分" not in prob_text
        # out 不出理由
        assert "不提供打分理由" in explain_text


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
