"""端到端 App 流程测试。"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from condition_recommender import recommend
from features.descriptors import compute_pair_features
from predictor.tree_model import TreeFilmPredictor, train_tree_baseline
from report_generator.exporter import generate_report


class TestDescriptorPipeline:
    def test_compute_pair_features(self):
        feats = compute_pair_features(
            "O=CC1=C(C=O)C(=O)C(C=O)=C1O",  # Tp
            "Nc1ccc(N)cc1",  # Pa
        )
        assert len(feats) > 30
        assert "ald_mw" in feats
        assert "amine_mw" in feats
        assert "pair_site_ratio" in feats


class TestConditionRecommender:
    def test_recommend_returns_conditions(self):
        cond = recommend(
            "O=CC1=C(C=O)C(=O)C(C=O)=C1O",
            "Nc1ccc(N)cc1",
        )
        assert "method" in cond
        assert "temperature" in cond
        assert "catalyst" in cond
        assert cond["method"] is not None


class TestTreeModel:
    def test_train_and_predict(self):
        df = pd.read_csv(PROJECT_ROOT / "data" / "raw" / "v5_train_stage1.csv")
        # 为了测试速度，用前 500 行
        df = df.head(500)
        predictor = TreeFilmPredictor()
        metrics = predictor.train(df, group_by="aldehyde")
        assert "mae" in metrics
        assert metrics["n_samples"] >= 490  # 可能有少量 SMILES 缺失被跳过

        prob = predictor.predict_single(
            "O=CC1=C(C=O)C(=O)C(C=O)=C1O",
            "Nc1ccc(N)cc1",
        )
        assert 0 <= prob <= 1


class TestReportGenerator:
    def test_generate_report(self):
        pred = {
            "gnn_probability": 0.92,
            "tree_probability": 0.85,
            "ensemble_probability": 0.885,
        }
        cond = recommend(
            "O=CC1=C(C=O)C(=O)C(C=O)=C1O",
            "Nc1ccc(N)cc1",
        )
        path = generate_report(
            "O=CC1=C(C=O)C(=O)C(C=O)=C1O",
            "Nc1ccc(N)cc1",
            pred,
            cond,
            output_path=PROJECT_ROOT / "reports" / "test_report.docx",
        )
        assert path.exists()
        assert path.stat().st_size > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
