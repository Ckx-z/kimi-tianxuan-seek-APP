"""基础数据测试。"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

# 确保 src/ 在路径中
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from data.import_data import import_data, DATA_RAW_DIR


class TestDataImport:
    def test_data_files_exist(self):
        assert (DATA_RAW_DIR / "v5_train_stage1.csv").exists()
        assert (DATA_RAW_DIR / "merged_monomer_pool.csv").exists()

    def test_v5_columns(self):
        df = pd.read_csv(DATA_RAW_DIR / "v5_train_stage1.csv")
        expected = [
            "paper_id", "group_id", "source_db",
            "aldehyde_smiles", "amine_smiles",
            "aldehyde_name", "amine_name",
            "stoichiometry", "solvent", "temperature", "catalyst",
            "synthesis_route", "interface_type",
            "is_film", "film_quality", "original_is_film",
        ]
        for col in expected:
            assert col in df.columns, f"缺少列 {col}"

    def test_smiles_missing_rate_low(self):
        """SMILES 缺失率应很低（v5 数据集已大幅补全）。"""
        df = pd.read_csv(DATA_RAW_DIR / "v5_train_stage1.csv")
        missing = (df["aldehyde_smiles"].isna() | df["amine_smiles"].isna()).mean()
        # 旧项目记录早期缺失率约 29.5%，但 v5 清洗后已降至 <1%
        assert missing <= 0.05, f"SMILES 缺失率异常：{missing:.2%}"

    def test_label_distribution(self):
        df = pd.read_csv(DATA_RAW_DIR / "v5_train_stage1.csv")
        labels = df["is_film"].dropna().unique()
        assert set(labels).issubset({0.0, 0.7, 0.8, 1.0})


class TestImportScript:
    def test_import_data_runs(self, tmp_path):
        # 使用临时目录测试 import_data 逻辑是否可运行
        # 这里仅验证函数不报错（因为数据已经复制好）
        import_data(include_optional=True)
