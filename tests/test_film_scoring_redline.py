"""成膜打分阶段一（v1.6.1）测试：低交联度红线 + 组合级训练覆盖 + 保守融合。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for p in (str(PROJECT_ROOT), str(PROJECT_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.predictor import fusion, pair_pool  # noqa: E402
from src.predictor.ood import check_networkability, check_ood  # noqa: E402

BA = "O=Cc1ccccc1"                       # 苯甲醛（单官能醛）
AN = "Nc1ccccc1"                         # 苯胺（单官能胺）
TP = "O=CC1=C(C=O)C(=O)C(C=O)=C1O"       # Tp 三醛
PA = "Nc1ccc(N)cc1"                      # 对苯二胺（双官能胺）
EDA = "NCCN"                             # 乙二胺（双伯胺，脂肪族）


# ---------------------------------------------------------------- 网络能力红线

def test_networkability_single_functional_redline():
    r = check_networkability(BA, AN)
    assert r["level"] == "warning"
    assert r["details"]["can_network"] is False
    assert r["details"]["n_aldehyde_sites"] == 1
    assert r["details"]["n_amine_sites"] == 1
    assert "低交联度" in r["reasons"][0]


def test_networkability_good_pairs_pass():
    assert check_networkability(TP, PA)["details"]["can_network"] is True   # 3×2
    assert check_networkability(BA, EDA)["details"]["can_network"] is False  # 1×2
    # 2×2 可成网
    diald = "O=Cc1ccc(C=O)cc1"  # 对苯二甲醛
    assert check_networkability(diald, PA)["details"]["can_network"] is True


def test_networkability_unparsable_no_crash():
    r = check_networkability("not-a-smiles", AN)
    assert r["level"] == "warning"  # 位点数按 0 计 → 不可成网（保守）
    assert r["details"]["n_aldehyde_sites"] == 0


def test_check_ood_includes_networkability():
    r = check_ood(BA, AN, pool=None, envelope=None)
    assert "networkability" in r["checks"]
    assert r["checks"]["networkability"]["level"] == "warning"
    assert r["level"] == "warning"
    assert any("低交联度" in x for x in r["reasons"])
    # 良对不触发
    r2 = check_ood(TP, PA, pool=None, envelope=None)
    assert r2["checks"]["networkability"]["level"] == "none"
    assert r2["level"] == "none"


# ---------------------------------------------------------------- 融合口径

def _redline_ood():
    """真实 check_ood 形状：can_network 位于 networkability.details 内。"""
    return {"level": "warning", "reasons": ["低交联度"],
            "checks": {"networkability": {
                "level": "warning", "reasons": ["低交联度"],
                "details": {"n_aldehyde_sites": 1, "n_amine_sites": 1,
                            "min_functionality": 1, "can_network": False}}}}


def _ok_ood():
    return {"level": "none", "reasons": [],
            "checks": {"networkability": {
                "level": "none", "reasons": [],
                "details": {"can_network": True}}}}


def test_headline_redline_clamps_both():
    """苯甲醛+苯胺：tree 0.447 / gnn 0.647 → 红线钳制 ≤0.25。"""
    score, src = fusion.headline_score({
        "tree_probability": 0.447, "gnn_probability": 0.647,
        "ood": _redline_ood(), "pair_seen": True})
    assert src == "both"
    assert score <= 0.25
    assert abs(score - min(0.25, 0.5 * 0.447)) < 1e-9


def test_headline_redline_clamps_single_model():
    score, src = fusion.headline_score({
        "tree_probability": 0.778, "ood": _redline_ood(), "pair_seen": True})
    assert src == "tree"
    assert abs(score - min(0.25, 0.5 * 0.778)) < 1e-9


def test_headline_normal_pair_keeps_max():
    """良对（可成网）保持 max 口径（回归护栏：Tp+Pa 分数不降）。"""
    score, src = fusion.headline_score({
        "tree_probability": 0.821, "gnn_probability": 0.598,
        "ood": _ok_ood(),
        "pair_seen": True})
    assert src == "both"
    assert abs(score - 0.821) < 1e-9


def test_headline_pair_unseen_shrinks_gnn():
    """组合未见训练集：GNN 分量 ×0.8 后再取 max。"""
    score, _ = fusion.headline_score({
        "tree_probability": 0.5, "gnn_probability": 0.9,
        "ood": _ok_ood(),
        "pair_seen": False})
    assert abs(score - 0.9 * 0.8) < 1e-9


def test_score_flags():
    flags = fusion.score_flags({
        "tree_probability": 0.9, "gnn_probability": 0.3,
        "ood": _redline_ood(), "pair_seen": False})
    assert flags == {"redline": True, "divergence": True,
                     "gnn_pair_unseen": True}
    flags2 = fusion.score_flags({
        "tree_probability": 0.82, "gnn_probability": 0.60,
        "ood": _ok_ood(),
        "pair_seen": True})
    assert flags2 == {"redline": False, "divergence": False,
                      "gnn_pair_unseen": False}


# ---------------------------------------------------------------- 组合池

def test_pair_pool(tmp_path, monkeypatch):
    csv = tmp_path / "train.csv"
    csv.write_text(
        "paper_id,aldehyde_smiles,amine_smiles\n"
        f"1,{TP},{PA}\n"
        f"2,{TP},Nc1ccc(N)cc1-c1ccc(N)cc1\n",
        encoding="utf-8")
    monkeypatch.setattr(pair_pool, "PAIR_POOL_CSV", csv)
    monkeypatch.setattr(pair_pool, "_pair_set", None)
    monkeypatch.setattr(pair_pool, "_load_failed", False)
    assert pair_pool.pair_seen(TP, PA) is True
    assert pair_pool.pair_seen(BA, AN) is False   # 苯甲醛+苯胺未见过
    assert pair_pool.pair_seen(BA, EDA) is False


def test_pair_pool_missing_file_falls_back_seen(tmp_path, monkeypatch):
    monkeypatch.setattr(pair_pool, "PAIR_POOL_CSV", tmp_path / "nope.csv")
    monkeypatch.setattr(pair_pool, "_pair_set", None)
    monkeypatch.setattr(pair_pool, "_load_failed", False)
    assert pair_pool.pair_seen(BA, AN) is True  # 降级为「见过」，不收缩


# ---------------------------------------------------------------- API 集成

def test_api_predict_redline_clamps(monkeypatch):
    """/api/predict 端到端：红线组合主分钳制 + flags 透出。"""
    from fastapi.testclient import TestClient

    from api import deps
    from api.main import app

    client = TestClient(app)

    class _RedlinePredictor:
        def predict(self, a, b):
            return {
                "ald_smiles": a, "amine_smiles": b,
                "tree_probability": 0.447, "gnn_probability": 0.647,
                "tree_std": 0.02, "gnn_std": 0.04,
                "tree_model_name": "tree_v4_ens",
                "tree_route": "in_pool",
                "ood": _redline_ood(),
                "pair_seen": False,
            }

    monkeypatch.setattr(deps, "_PREDICTOR", _RedlinePredictor())
    r = client.post("/api/predict",
                    json={"ald_smiles": BA, "amine_smiles": AN})
    assert r.status_code == 200
    d = r.json()
    assert d["score"] is not None and d["score"] <= 0.25
    assert d["score_policy"] == "max_tree_gnn_redline"
    assert d["score_flags"] == {"redline": True, "divergence": False,
                                "gnn_pair_unseen": True}
    assert d["ood"]["level"] == "warning"
