"""P4a 实验记录后端测试：experiment_no 必填 + conditions 新键（任务A）。

覆盖：
- experiment_no 必填（空串/空白/缺省 → ValueError），关联与游离两路径；
- experiment_no 落盘为独立字段 + 并入 notes 前缀；
- conditions 新标准键 solvent_1 / solvent_2 / eluent 等九个；
- 旧 solvent 单键兼容映射到 solvent_1（旧键原样保留）；
- 契约字段逐项断言（schema 同步 data/rag_export/README.md Schema 2）。

所有写操作通过 monkeypatch 把目录指到 tmp_path，不污染真实数据目录。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from favorites import store as fav_store  # noqa: E402
from records import store as rec_store  # noqa: E402

TP = "O=CC1=C(C=O)C(=O)C(C=O)=C1O"
PA = "Nc1ccc(N)cc1"

STANDARD_CONDITION_KEYS = (
    "solvent_1",
    "solvent_2",
    "eluent",
    "modulator",
    "catalyst",
    "temperature_c",
    "time_days",
    "vessel",
    "addition_order",
)


@pytest.fixture
def rec_dirs(tmp_path, monkeypatch):
    fav_d = tmp_path / "favorites"
    rec_d = tmp_path / "records"
    monkeypatch.setattr(fav_store, "FAVORITES_DIR", fav_d)
    monkeypatch.setattr(rec_store, "RECORDS_DIR", rec_d)
    return fav_d, rec_d


class TestExperimentNoRequired:
    def test_missing_raises_linked(self, rec_dirs):
        fav = fav_store.add_favorite(TP, PA)
        with pytest.raises(ValueError):
            rec_store.create_record(fav["id"], {}, "film")

    def test_empty_raises_linked(self, rec_dirs):
        fav = fav_store.add_favorite(TP, PA)
        for bad in ("", "   ", None):
            with pytest.raises(ValueError):
                rec_store.create_record(
                    fav["id"], {}, "film", experiment_no=bad,
                )

    def test_empty_raises_orphan(self, rec_dirs):
        for bad in ("", "   ", None):
            with pytest.raises(ValueError):
                rec_store.create_record(
                    favorite_id=None, aldehyde_smiles=TP, amine_smiles=PA,
                    outcome="film", experiment_no=bad,
                )

    def test_saved_as_field_linked(self, rec_dirs):
        fav_d, rec_d = rec_dirs
        fav = fav_store.add_favorite(TP, PA)
        rec = rec_store.create_record(
            fav["id"], {}, "film", experiment_no="A5",
        )
        assert rec["experiment_no"] == "A5"
        saved = json.loads(
            (rec_d / f"{rec['record_id']}.json").read_text(encoding="utf-8")
        )
        assert saved["experiment_no"] == "A5"

    def test_saved_as_field_orphan(self, rec_dirs):
        _, rec_d = rec_dirs
        rec = rec_store.create_record(
            favorite_id=None, aldehyde_smiles=TP, amine_smiles=PA,
            outcome="failed", experiment_no="G2-3",
        )
        assert rec["experiment_no"] == "G2-3"
        saved = json.loads(
            (rec_d / f"{rec['record_id']}.json").read_text(encoding="utf-8")
        )
        assert saved["experiment_no"] == "G2-3"

    def test_whitespace_stripped(self, rec_dirs):
        fav = fav_store.add_favorite(TP, PA)
        rec = rec_store.create_record(
            fav["id"], {}, "film", experiment_no="  A5  ",
        )
        assert rec["experiment_no"] == "A5"

    def test_prefixed_into_notes(self, rec_dirs):
        fav = fav_store.add_favorite(TP, PA)
        rec = rec_store.create_record(
            fav["id"], {}, "film", notes="顺利", experiment_no="A5",
        )
        assert rec["notes"].startswith("实验编号：A5")
        assert "顺利" in rec["notes"]

    def test_prefixed_into_empty_notes(self, rec_dirs):
        fav = fav_store.add_favorite(TP, PA)
        rec = rec_store.create_record(fav["id"], {}, "film", experiment_no="A5")
        assert rec["notes"] == "实验编号：A5"

    def test_legacy_positional_call_with_experiment_no(self, rec_dirs):
        fav = fav_store.add_favorite(TP, PA)
        rec = rec_store.create_record(
            fav["id"], {}, "film", "强度好", "备注", "ckx", experiment_no="B1",
        )
        assert rec["experiment_no"] == "B1"
        assert rec["notes"].startswith("实验编号：B1")
        assert "备注" in rec["notes"]


class TestConditionKeysP4a:
    def test_nine_standard_keys_present(self, rec_dirs):
        fav = fav_store.add_favorite(TP, PA)
        rec = rec_store.create_record(fav["id"], {}, "film", experiment_no="A5")
        for k in STANDARD_CONDITION_KEYS:
            assert k in rec["conditions"], f"缺少标准键 {k}"
            assert rec["conditions"][k] == ""
        # 旧默认键 solvent 不再是标准键（未提供时不出现）
        assert "solvent" not in rec["conditions"]

    def test_new_keys_accepted(self, rec_dirs):
        fav = fav_store.add_favorite(TP, PA)
        rec = rec_store.create_record(
            fav["id"],
            {"solvent_1": "甲苯", "solvent_2": "二氧六环", "eluent": "氯仿"},
            "film", experiment_no="A5",
        )
        cond = rec["conditions"]
        assert cond["solvent_1"] == "甲苯"
        assert cond["solvent_2"] == "二氧六环"
        assert cond["eluent"] == "氯仿"

    def test_legacy_solvent_mapped_to_solvent_1(self, rec_dirs):
        fav = fav_store.add_favorite(TP, PA)
        rec = rec_store.create_record(
            fav["id"], {"solvent": "BTF"}, "film", experiment_no="A5",
        )
        cond = rec["conditions"]
        assert cond["solvent_1"] == "BTF"      # 兼容映射
        assert cond["solvent"] == "BTF"        # 旧键原样保留（额外字段）

    def test_explicit_solvent_1_not_overwritten(self, rec_dirs):
        fav = fav_store.add_favorite(TP, PA)
        rec = rec_store.create_record(
            fav["id"], {"solvent": "旧", "solvent_1": "新"},
            "film", experiment_no="A5",
        )
        assert rec["conditions"]["solvent_1"] == "新"

    def test_extra_keys_preserved(self, rec_dirs):
        fav = fav_store.add_favorite(TP, PA)
        rec = rec_store.create_record(
            fav["id"], {"aging_days": 2}, "film", experiment_no="A5",
        )
        assert rec["conditions"]["aging_days"] == 2


class TestContractSchemaP4a:
    """逐字段对照 data/rag_export/README.md Schema 2 experiment_record。"""

    def test_record_fields(self, rec_dirs):
        _, rec_d = rec_dirs
        fav = fav_store.add_favorite(TP, PA)
        rec = rec_store.create_record(
            favorite_id=fav["id"],
            conditions={"solvent_1": "甲苯", "eluent": "氯仿", "temperature_c": 120},
            outcome="partial", strength="粗糙", notes="备注", operator="ckx",
            experiment_no="A5",
        )
        assert rec["schema_version"] == "1.0"
        assert rec["record_type"] == "experiment_record"
        assert rec["record_id"].startswith("rec_")
        assert rec["experiment_no"] == "A5"
        assert rec["favorite_id"] == fav["id"]
        assert set(rec["aldehyde"]) == {"smiles", "cas", "name"}
        assert set(rec["amine"]) == {"smiles", "cas", "name"}
        assert rec["prediction_id"] is None
        assert rec["outcome"] == "partial"
        assert rec["failure_class"] is None
        assert rec["strength"] == "粗糙"
        assert rec["attachments"] == []
        assert rec["operator"] == "ckx"
        assert rec["date"]
        assert rec["minimax_plan_no"] is None
        # 落盘字段与返回一致
        saved = json.loads(
            (rec_d / f"{rec['record_id']}.json").read_text(encoding="utf-8")
        )
        assert saved == rec
