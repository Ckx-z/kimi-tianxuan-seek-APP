"""双模型路由（D22 上线，D23 修订为 routed_strict）单元测试。

覆盖：
- MonomerPool 单体池判定与路由键
- RoutedTreePredictor 模型切换（routed_strict：仅双未见 → noTE，其余含"一新一熟" → v4）
- FilmPredictor 路由模式输出与路由原因标注
- 打分理由（SHAP 归因）跟随实际路由的模型（v4 走 TE 填充 / noTE 走原 v3 路径）
- 向后兼容：单模型加载路径、TreeFilmPredictor 直接加载 tree_v4_noTE.pkl

依赖真实模型文件（models/tree_v4_ens.pkl、tree_v4_noTE_ens.pkl、monomer_pool.json），
缺失时整文件跳过；归因用例额外需要 shap（无 shap 环境单独跳过）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from predictor import FilmPredictor  # noqa: E402
from predictor.routing import (  # noqa: E402
    EXTRAP_MODEL_PATH,
    MONOMER_POOL_PATH,
    POOL_MODEL_PATH,
    ROUTE_ALD_UNSEEN,
    ROUTE_AMINE_UNSEEN,
    ROUTE_BOTH_UNSEEN,
    ROUTE_IN_POOL,
    MonomerPool,
    RoutedTreePredictor,
)
from predictor.tree_model import TreeFilmPredictor  # noqa: E402

TP = "O=CC1=C(C=O)C(=O)C(C=O)=C1O"  # 经典醛单体（训练池内）
PA = "Nc1ccc(N)cc1"  # 经典胺单体（训练池内）
UNSEEN_ALD = "O=Cc1csc(C=O)c1"  # 不在训练映射中的醛（噻吩二醛）
UNSEEN_AMINE = "Nc1ccnnc1"  # 不在训练映射中的胺

_ROUTING_ASSETS = (POOL_MODEL_PATH, EXTRAP_MODEL_PATH, MONOMER_POOL_PATH)


def _assets_missing() -> bool:
    return any(not p.exists() for p in _ROUTING_ASSETS)


pytestmark = pytest.mark.skipif(_assets_missing(), reason="路由模型/单体池文件不存在")


class TestMonomerPool:
    """单体池成员判定与路由键。"""

    def test_membership(self):
        pool = MonomerPool.load(MONOMER_POOL_PATH)
        assert pool.ald_seen(TP)
        assert pool.amine_seen(PA)
        assert not pool.ald_seen(UNSEEN_ALD)
        assert not pool.amine_seen(UNSEEN_AMINE)

    def test_route_key(self):
        pool = MonomerPool.load(MONOMER_POOL_PATH)
        assert pool.route_key(TP, PA) == ROUTE_IN_POOL
        assert pool.route_key(UNSEEN_ALD, PA) == ROUTE_ALD_UNSEEN
        assert pool.route_key(TP, UNSEEN_AMINE) == ROUTE_AMINE_UNSEEN
        assert pool.route_key(UNSEEN_ALD, UNSEEN_AMINE) == ROUTE_BOTH_UNSEEN


@pytest.fixture(scope="module")
def router():
    r = RoutedTreePredictor()
    r.load()
    return r


class TestRoutedTreePredictor:
    """路由切换（routed_strict，D23）：仅双未见走 noTE，其余（含一新一熟）走 v4。"""

    def test_both_seen_routes_to_v4(self, router):
        model, key, reason = router.route_for(TP, PA)
        assert key == ROUTE_IN_POOL
        assert model is router.pool_model
        assert model.model_path.stem == "tree_v4_ens"
        assert model.te_rates is not None  # v4 走 TE 填充路径
        assert "已知单体组合" in reason

    def test_mixed_unseen_routes_to_v4(self, router):
        """一新一熟（非双未见）→ tree_v4：单侧 TE 仍有强信号（exp_008 混合桶 +0.024~0.031）。"""
        for ald, amine, key, side in ((UNSEEN_ALD, PA, ROUTE_ALD_UNSEEN, "醛"),
                                      (TP, UNSEEN_AMINE, ROUTE_AMINE_UNSEEN, "胺")):
            model, got_key, reason = router.route_for(ald, amine)
            assert got_key == key
            assert model is router.pool_model
            assert model.model_path.stem == "tree_v4_ens"
            assert model.te_rates is not None
            assert "非双未见" in reason and side in reason and "tree_v4" in reason

    def test_both_unseen_routes_to_noTE(self, router):
        """双未见 → tree_v4_noTE（外推模式）：双留出最强臂，切换后兜底不变。"""
        model, key, reason = router.route_for(UNSEEN_ALD, UNSEEN_AMINE)
        assert key == ROUTE_BOTH_UNSEEN
        assert model is router.extrap_model
        assert model.model_path.stem == "tree_v4_noTE_ens"
        assert model.te_rates is None  # noTE 走原 v3 无先验路径
        assert "双未见" in reason and "外推模式" in reason

    def test_route_model_mapping_strict(self, router):
        """边界：四个路由键的模型映射——仅 ROUTE_BOTH_UNSEEN 映射到外推臂。"""
        expected = {
            ROUTE_IN_POOL: router.pool_model,
            ROUTE_ALD_UNSEEN: router.pool_model,
            ROUTE_AMINE_UNSEEN: router.pool_model,
            ROUTE_BOTH_UNSEEN: router.extrap_model,
        }
        for (ald, amine), (key, model) in {
            (TP, PA): (ROUTE_IN_POOL, router.pool_model),
            (UNSEEN_ALD, PA): (ROUTE_ALD_UNSEEN, router.pool_model),
            (TP, UNSEEN_AMINE): (ROUTE_AMINE_UNSEEN, router.pool_model),
            (UNSEEN_ALD, UNSEEN_AMINE): (ROUTE_BOTH_UNSEEN, router.extrap_model),
        }.items():
            got_model, got_key, _ = router.route_for(ald, amine)
            assert got_key == key
            assert got_model is expected[got_key] is model

    def test_predict_with_info(self, router):
        info = router.predict_with_info(TP, PA)
        assert 0.0 <= info["probability"] <= 1.0
        assert info["model_name"] == "tree_v4_ens"
        assert info["ald_seen"] and info["amine_seen"]
        # 一新一熟：routed_strict 下也走 v4
        info_mix = router.predict_with_info(UNSEEN_ALD, PA)
        assert info_mix["model_name"] == "tree_v4_ens"
        assert info_mix["route"] == ROUTE_ALD_UNSEEN
        assert not info_mix["ald_seen"] and info_mix["amine_seen"]
        info2 = router.predict_with_info(UNSEEN_ALD, UNSEEN_AMINE)
        assert info2["model_name"] == "tree_v4_noTE_ens"
        assert not info2["ald_seen"] and not info2["amine_seen"]

    def test_routed_prediction_matches_underlying_model(self, router):
        """路由预测必须等于被路由模型自己的预测（无额外变换）。"""
        assert router.predict_single(TP, PA) == pytest.approx(
            router.pool_model.predict_single(TP, PA), abs=1e-9)
        # 一新一熟：routed_strict 后等于池内臂 v4 的预测
        assert router.predict_single(UNSEEN_ALD, PA) == pytest.approx(
            router.pool_model.predict_single(UNSEEN_ALD, PA), abs=1e-9)
        # 双未见：仍等于外推臂 noTE 的预测
        assert router.predict_single(UNSEEN_ALD, UNSEEN_AMINE) == pytest.approx(
            router.extrap_model.predict_single(UNSEEN_ALD, UNSEEN_AMINE), abs=1e-9)


@pytest.fixture(scope="module")
def routed_film_predictor():
    return FilmPredictor(use_gnn=False, use_tree=True)


class TestFilmPredictorRouting:
    """FilmPredictor 路由模式：结果标注实际使用模型与路由原因。"""

    def test_routing_mode_active(self, routed_film_predictor):
        p = routed_film_predictor
        assert p.router is not None and p.tree is None
        assert p.tree_available

    def test_known_pair_uses_v4_with_reason(self, routed_film_predictor):
        r = routed_film_predictor.predict(TP, PA)
        assert r["tree_model_name"] == "tree_v4_ens"
        assert r["tree_route"] == ROUTE_IN_POOL
        assert "已知单体组合" in r["tree_route_reason"]
        assert 0.0 <= r["tree_probability"] <= 1.0
        # 无 GNN 时综合概率 = 树模型概率
        assert r["ensemble_probability"] == pytest.approx(r["tree_probability"])

    def test_unseen_pair_uses_noTE_with_reason(self, routed_film_predictor):
        r = routed_film_predictor.predict(UNSEEN_ALD, UNSEEN_AMINE)
        assert r["tree_model_name"] == "tree_v4_noTE_ens"
        assert r["tree_route"] == ROUTE_BOTH_UNSEEN
        assert "外推模式" in r["tree_route_reason"]

    def test_mixed_pair_uses_v4_with_reason(self, routed_film_predictor):
        """一新一熟（routed_strict，D23）→ tree_v4，并标注非双未见原因。"""
        r = routed_film_predictor.predict(UNSEEN_ALD, PA)
        assert r["tree_model_name"] == "tree_v4_ens"
        assert r["tree_route"] == ROUTE_ALD_UNSEEN
        assert "非双未见" in r["tree_route_reason"]

    def test_get_tree_for_follows_route(self, routed_film_predictor):
        tree, info = routed_film_predictor.get_tree_for(TP, PA)
        assert tree.model_path.stem == "tree_v4_ens" and info["route"] == ROUTE_IN_POOL
        # 一新一熟：routed_strict 后跟随池内臂 v4
        tree2, info2 = routed_film_predictor.get_tree_for(TP, UNSEEN_AMINE)
        assert tree2.model_path.stem == "tree_v4_ens" and info2["route"] == ROUTE_AMINE_UNSEEN
        # 双未见：仍跟随外推臂 noTE
        tree3, info3 = routed_film_predictor.get_tree_for(UNSEEN_ALD, UNSEEN_AMINE)
        assert tree3.model_path.stem == "tree_v4_noTE_ens" and info3["route"] == ROUTE_BOTH_UNSEEN


class TestAttributionFollowsRoute:
    """打分理由跟随实际路由的模型：v4 有 TE 先验组，noTE 无。"""

    @pytest.fixture(scope="class")
    def shap_ok(self):
        pytest.importorskip("shap", reason="当前环境无 shap，跳过归因跟随测试")

    def test_explain_matches_routed_prediction(self, shap_ok, routed_film_predictor):
        from models.attribution import explain_pair_for_app

        # routed_strict（D23）：一新一熟也走 v4（expect_te=True），仅双未见走 noTE
        for ald, amine, expect_te in ((TP, PA, True),
                                      (UNSEEN_ALD, PA, True),
                                      (UNSEEN_ALD, UNSEEN_AMINE, False)):
            tree, _ = routed_film_predictor.get_tree_for(ald, amine)
            exp = explain_pair_for_app(tree.model, tree.feature_cols, ald, amine,
                                       feature_flags=tree.feature_flags,
                                       te_rates=tree.te_rates)
            pred = routed_film_predictor.predict(ald, amine)["tree_probability"]
            assert exp["predicted_film_score"] == pytest.approx(pred, abs=0.05)  # 集成均值 vs 成员[0] 归因近似
            assert ("te_ald_film_rate" in tree.feature_cols) is expect_te
            assert (exp["group_contributions"].get("prior", 0.0) > 0) is expect_te


class TestBackwardCompat:
    """向后兼容：单模型加载路径 / TreeFilmPredictor / 默认路径不变。"""

    def test_explicit_path_single_model_mode(self):
        p = FilmPredictor(use_gnn=False, use_tree=True,
                          tree_model_path=PROJECT_ROOT / "models" / "tree_v3.pkl")
        assert p.router is None and p.tree is not None
        r = p.predict(TP, PA)
        assert r["tree_model_name"] == "tree_v3"
        assert "tree_route" not in r and "tree_route_reason" not in r
        tree, info = p.get_tree_for(TP, PA)
        assert tree is p.tree and info is None

    def test_use_routing_false_loads_default_v3(self):
        p = FilmPredictor(use_gnn=False, use_tree=True, use_routing=False)
        assert p.router is None
        assert p.tree.model_path.stem == "tree_v3"

    def test_tree_v4_noTE_pkl_self_describing(self):
        """tree_v4_noTE.pkl 自描述：142 维无 TE 列、te_rates=None、开关恢复。"""
        t = TreeFilmPredictor(EXTRAP_MODEL_PATH)
        t.load()
        assert len(t.feature_cols) == 142
        assert "te_ald_film_rate" not in t.feature_cols
        assert t.te_rates is None and t.fp_params is None
        assert t.feature_flags.get("use_3d") is True
        assert t.feature_flags.get("use_interaction") is True
        prob = t.predict_single(TP, PA)
        assert 0.0 <= prob <= 1.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
