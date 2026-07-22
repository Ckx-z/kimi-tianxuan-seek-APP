"""P2 后端模块测试：收藏夹 / 实验记录 / 方案卡。

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
from recommend.plan_card import generate_plan_card  # noqa: E402

# 训练语料里真实存在的单体（Tp / Pa，paper_id=101 同组合出现）
TP = "O=CC1=C(C=O)C(=O)C(C=O)=C1O"
PA = "Nc1ccc(N)cc1"
# 内置库单体
A2_SMILES = "O=Cc1c(F)c(F)c(C=O)c(F)c1F"  # A2, cas 3217-47-8
TAPT_SMILES = "Nc1ccc(-c2nc(-c3ccc(N)cc3)nc(-c3ccc(N)cc3)n2)cc1"  # TAPT
# 酰肼单体（H 组风格，腙键体系）
HYDRAZIDE = "NNC(=O)c1cc(C(=O)NN)c(C=O)cc1C=O"


@pytest.fixture()
def fav_dir(tmp_path, monkeypatch):
    """收藏目录隔离到 tmp_path，返回该目录。"""
    d = tmp_path / "favorites"
    monkeypatch.setattr(fav_store, "FAVORITES_DIR", d)
    return d


@pytest.fixture()
def rec_dirs(tmp_path, monkeypatch):
    """收藏 + 记录目录同时隔离到 tmp_path。"""
    fav_d = tmp_path / "favorites"
    rec_d = tmp_path / "records"
    monkeypatch.setattr(fav_store, "FAVORITES_DIR", fav_d)
    monkeypatch.setattr(rec_store, "RECORDS_DIR", rec_d)
    return fav_d, rec_d


# ---------------------------------------------------------------- 收藏夹

class TestAddFavorite:
    def test_schema_and_file(self, fav_dir):
        fav = fav_store.add_favorite(TP, PA, notes="第一组候选")
        assert set(fav) == {
            "id",
            "aldehyde",
            "amine",
            "created_at",
            "notes",
            "latest_prediction",
            "references",
            "experiment_record_ids",
        }
        assert fav["id"].startswith("fav_")
        assert fav["aldehyde"]["smiles"] and fav["amine"]["smiles"]
        assert set(fav["aldehyde"]) == {"smiles", "cas", "name"}
        assert fav["latest_prediction"] is None
        assert fav["experiment_record_ids"] == []
        assert isinstance(fav["references"], list)
        # 落盘文件存在且内容一致
        saved = json.loads((fav_dir / f"{fav['id']}.json").read_text(encoding="utf-8"))
        assert saved["id"] == fav["id"]

    def test_cas_name_backfilled_from_builtin(self, fav_dir):
        fav = fav_store.add_favorite(A2_SMILES, TAPT_SMILES)
        assert fav["aldehyde"]["cas"] == "3217-47-8"
        assert fav["aldehyde"]["name"] == "A2"
        assert fav["amine"]["cas"] == "14544-47-9"
        assert fav["amine"]["name"] == "TAPT"

    def test_explicit_name_wins(self, fav_dir):
        fav = fav_store.add_favorite(A2_SMILES, TAPT_SMILES, ald_name="我的醛")
        assert fav["aldehyde"]["name"] == "我的醛"
        assert fav["aldehyde"]["cas"] == "3217-47-8"  # CAS 仍自动填充

    def test_unknown_monomer_empty_cas(self, fav_dir):
        fav = fav_store.add_favorite("O=CC=O", "NCCN")
        assert fav["aldehyde"]["cas"] == ""
        assert fav["aldehyde"]["name"] == ""

    def test_ids_increment(self, fav_dir):
        f1 = fav_store.add_favorite(TP, PA)
        f2 = fav_store.add_favorite(TP, PA)
        assert f1["id"] != f2["id"]
        assert f1["id"] < f2["id"]

    def test_auto_references_attached(self, fav_dir):
        fav = fav_store.add_favorite(TP, PA)
        assert len(fav["references"]) >= 1
        ref = fav["references"][0]
        assert ref["source"] == "auto-matched"
        assert ref["match_type"] in ("both", "aldehyde", "amine")
        assert ref["count"] >= 1


class TestFavoriteCrud:
    def test_list_and_get(self, fav_dir):
        f1 = fav_store.add_favorite(TP, PA)
        f2 = fav_store.add_favorite(A2_SMILES, TAPT_SMILES)
        favs = fav_store.list_favorites()
        assert {f["id"] for f in favs} == {f1["id"], f2["id"]}
        got = fav_store.get_favorite(f1["id"])
        assert got["id"] == f1["id"]

    def test_list_empty_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(fav_store, "FAVORITES_DIR", tmp_path / "nope")
        assert fav_store.list_favorites() == []

    def test_get_missing_returns_none(self, fav_dir):
        assert fav_store.get_favorite("fav_20990101_999") is None
        assert fav_store.get_favorite("bad-id") is None
        assert fav_store.get_favorite("") is None

    def test_update(self, fav_dir):
        fav = fav_store.add_favorite(TP, PA)
        updated = fav_store.update_favorite(fav["id"], notes="改备注")
        assert updated["notes"] == "改备注"
        assert fav_store.get_favorite(fav["id"])["notes"] == "改备注"

    def test_update_cannot_change_id(self, fav_dir):
        fav = fav_store.add_favorite(TP, PA)
        updated = fav_store.update_favorite(fav["id"], id="fav_hack")
        assert updated["id"] == fav["id"]

    def test_update_missing_raises(self, fav_dir):
        with pytest.raises(KeyError):
            fav_store.update_favorite("fav_20990101_999", notes="x")

    def test_delete(self, fav_dir):
        fav = fav_store.add_favorite(TP, PA)
        assert fav_store.delete_favorite(fav["id"]) is True
        assert fav_store.get_favorite(fav["id"]) is None
        assert fav_store.delete_favorite(fav["id"]) is False

    def test_corrupt_file_skipped(self, fav_dir):
        fav = fav_store.add_favorite(TP, PA)
        (fav_dir / "fav_20990101_001.json").write_text("{损坏", encoding="utf-8")
        ids = [f["id"] for f in fav_store.list_favorites()]
        assert ids == [fav["id"]]


class TestPredictionSnapshot:
    def test_update_snapshot(self, fav_dir):
        fav = fav_store.add_favorite(TP, PA)
        updated = fav_store.update_prediction_snapshot(
            fav["id"], {"score": 0.699, "std": 0.04, "arm": "tree_v4", "ood": "none"}
        )
        snap = updated["latest_prediction"]
        assert snap["score"] == 0.699
        assert snap["std"] == 0.04
        assert snap["arm"] == "tree_v4"
        assert snap["ood"] == "none"
        assert snap["date"]
        # 持久化
        assert fav_store.get_favorite(fav["id"])["latest_prediction"]["score"] == 0.699

    def test_snapshot_missing_fav_raises(self, fav_dir):
        with pytest.raises(KeyError):
            fav_store.update_prediction_snapshot("fav_20990101_999", {"score": 0.5})


class TestReferences:
    def test_add_reference(self, fav_dir):
        fav = fav_store.add_favorite(TP, PA)
        n0 = len(fav["references"])
        updated = fav_store.add_reference(
            fav["id"], "Nature 2020 COF film", doi="10.1000/x", note="支撑G2"
        )
        assert len(updated["references"]) == n0 + 1
        ref = updated["references"][-1]
        assert ref["title"] == "Nature 2020 COF film"
        assert ref["doi"] == "10.1000/x"
        assert ref["source"] == "user-added"
        assert ref["path_or_url"] == ""
        assert ref["note"] == "支撑G2"

    def test_add_reference_missing_fav_raises(self, fav_dir):
        with pytest.raises(KeyError):
            fav_store.add_reference("fav_20990101_999", "t")


class TestAutoMatchReferences:
    def test_real_corpus_both_first(self):
        refs = fav_store.auto_match_references(TP, PA)
        assert len(refs) >= 1
        # Tp+Pa 同组合在语料中存在，both 必须排最前
        assert refs[0]["match_type"] == "both"
        assert refs[0]["note"] == "报道过该醛胺组合"
        for r in refs:
            assert set(r) == {
                "title",
                "doi",
                "source",
                "path_or_url",
                "match_type",
                "count",
                "note",
            }
            assert r["source"] == "auto-matched"
            assert r["count"] >= 1

    def test_max_refs_respected(self):
        refs = fav_store.auto_match_references(TP, PA, max_refs=2)
        assert len(refs) <= 2

    def test_sorting_both_before_partial(self, tmp_path, monkeypatch):
        csv = tmp_path / "mini.csv"
        csv.write_text(
            "paper_id,aldehyde_smiles,amine_smiles\n"
            f"201,{TP},{PA}\n"          # both
            f"202,{TP},Nc1ccccc1\n"      # aldehyde only
            f"203,O=CC=O,{PA}\n",        # amine only
            encoding="utf-8",
        )
        monkeypatch.setattr(fav_store, "TRAIN_CSV", csv)
        refs = fav_store.auto_match_references(TP, PA)
        types = [r["match_type"] for r in refs]
        assert types == ["both", "aldehyde", "amine"]

    def test_invalid_input_returns_empty(self):
        assert fav_store.auto_match_references("junk_(((", "also junk") == []

    def test_missing_csv_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(fav_store, "TRAIN_CSV", tmp_path / "nope.csv")
        assert fav_store.auto_match_references(TP, PA) == []


# ---------------------------------------------------------------- 实验记录

class TestCreateRecord:
    def test_schema_matches_contract(self, rec_dirs):
        fav_d, rec_d = rec_dirs
        fav = fav_store.add_favorite(TP, PA)
        fav_store.update_prediction_snapshot(
            fav["id"], {"score": 0.65, "std": 0.05, "arm": "tree_v4", "ood": "none"}
        )
        rec = rec_store.create_record(
            fav["id"],
            conditions={"solvent": "甲苯", "temperature_c": 120, "time_days": 3},
            outcome="film",
            strength="连续膜，可剥离",
            notes="顺利",
            operator="ckx",
            experiment_no="A5",
        )
        # 契约必填字段
        assert rec["schema_version"] == "1.0"
        assert rec["record_type"] == "experiment_record"
        assert rec["record_id"].startswith("rec_")
        assert rec["favorite_id"] == fav["id"]
        assert set(rec["aldehyde"]) == {"smiles", "cas", "name"}
        assert rec["outcome"] == "film"
        assert rec["failure_class"] is None
        assert rec["attachments"] == []
        assert rec["date"]  # YYYY-MM-DD
        assert rec["minimax_plan_no"] is None
        # conditions 九个标准键齐全，未提供的留空（P4a：solvent→solvent_1/2+eluent）
        for k in (
            "solvent_1",
            "solvent_2",
            "eluent",
            "modulator",
            "catalyst",
            "temperature_c",
            "time_days",
            "vessel",
            "addition_order",
        ):
            assert k in rec["conditions"]
        assert rec["conditions"]["solvent_1"] == "甲苯"  # 旧 solvent 键兼容映射
        assert rec["conditions"]["solvent"] == "甲苯"    # 旧键原样保留
        assert rec["conditions"]["catalyst"] == ""
        # 预测快照冗余
        assert rec["prediction_snapshot"] == {"score": 0.65, "std": 0.05, "ood": "none"}
        # 落盘
        saved = json.loads(
            (rec_d / f"{rec['record_id']}.json").read_text(encoding="utf-8")
        )
        assert saved["record_id"] == rec["record_id"]

    def test_record_id_backlinked_to_favorite(self, rec_dirs):
        fav_d, _ = rec_dirs
        fav = fav_store.add_favorite(TP, PA)
        r1 = rec_store.create_record(fav["id"], {}, "film", experiment_no="T1")
        r2 = rec_store.create_record(fav["id"], {}, "failed", experiment_no="T1")
        ids = fav_store.get_favorite(fav["id"])["experiment_record_ids"]
        assert ids == [r1["record_id"], r2["record_id"]]

    def test_record_ids_independent_and_unique(self, rec_dirs):
        fav_d, _ = rec_dirs
        fav = fav_store.add_favorite(TP, PA)
        r1 = rec_store.create_record(fav["id"], {}, "film", experiment_no="T1")
        r2 = rec_store.create_record(fav["id"], {}, "film", experiment_no="T1")
        assert r1["record_id"] != r2["record_id"]
        assert not r1["record_id"].startswith(fav["id"])

    def test_invalid_outcome_raises(self, rec_dirs):
        fav_d, _ = rec_dirs
        fav = fav_store.add_favorite(TP, PA)
        for bad in ("success", "", "FILM", None):
            with pytest.raises(ValueError):
                rec_store.create_record(fav["id"], {}, bad, experiment_no="T1")

    def test_missing_favorite_raises(self, rec_dirs):
        with pytest.raises(KeyError):
            rec_store.create_record("fav_20990101_999", {}, "film", experiment_no="T1")

    def test_no_snapshot_when_never_predicted(self, rec_dirs):
        fav_d, _ = rec_dirs
        fav = fav_store.add_favorite(TP, PA)
        rec = rec_store.create_record(fav["id"], {}, "partial", experiment_no="T1")
        assert rec["prediction_snapshot"] is None
        assert rec["prediction_id"] is None


class TestListRecords:
    def test_list_and_filter(self, rec_dirs):
        fav_d, _ = rec_dirs
        f1 = fav_store.add_favorite(TP, PA)
        f2 = fav_store.add_favorite(A2_SMILES, TAPT_SMILES)
        r1 = rec_store.create_record(f1["id"], {}, "film", experiment_no="T1")
        rec_store.create_record(f1["id"], {}, "partial", experiment_no="T1")
        rec_store.create_record(f2["id"], {}, "failed", experiment_no="T1")
        assert len(rec_store.list_records()) == 3
        f1_recs = rec_store.list_records(favorite_id=f1["id"])
        assert len(f1_recs) == 2
        assert all(r["favorite_id"] == f1["id"] for r in f1_recs)

    def test_example_json_not_listed(self, rec_dirs):
        _, rec_d = rec_dirs
        rec_d.mkdir(parents=True)
        # 契约示例文件不应作为真实记录出现
        (rec_d / "example.json").write_text(
            json.dumps({"record_id": "rec_20260721_001", "record_type": "experiment_record"}),
            encoding="utf-8",
        )
        assert rec_store.list_records() == []

    def test_list_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rec_store, "RECORDS_DIR", tmp_path / "nope")
        assert rec_store.list_records() == []

    def test_get_record(self, rec_dirs):
        fav_d, _ = rec_dirs
        fav = fav_store.add_favorite(TP, PA)
        rec = rec_store.create_record(fav["id"], {}, "film", experiment_no="T1")
        got = rec_store.get_record(rec["record_id"])
        assert got["record_id"] == rec["record_id"]
        assert rec_store.get_record("rec_20990101_999") is None
        assert rec_store.get_record("bad") is None


# ---------------------------------------------------------------- 方案卡

class TestPlanCard:
    def test_default_conditions(self):
        card = generate_plan_card(TP, PA)
        cond = card["conditions"]
        assert cond["vessel"] == "35 mL Pyrex 管"
        assert "甲苯" in cond["solvent"] and "氯仿" in cond["solvent"]
        assert "苯胺" in cond["modulator"]
        assert "6M" in cond["catalyst"]
        assert cond["temperature_c"] == 120
        assert cond["time_days"] == "2-5"
        assert "v3.9" in card["defaults_note"]
        assert "可按组调整" in card["defaults_note"]

    def test_steps_cover_addition_order(self):
        card = generate_plan_card(TP, PA)
        steps = card["steps"]
        assert len(steps) >= 5
        joined = "".join(steps)
        assert "苯胺" in joined and "6M 乙酸" in joined and "120" in joined
        # 乙酸在最后加料步骤中出现得比胺单体步骤靠后
        idx_amine = next(i for i, s in enumerate(steps) if "胺单体" in s and "加入" in s)
        idx_acid = next(i for i, s in enumerate(steps) if "6M 乙酸" in s)
        assert idx_acid > idx_amine

    def test_checklist_from_real_failures(self):
        card = generate_plan_card(TP, PA)
        items = [c["item"] for c in card["checklist"]]
        details = "".join(c["detail"] for c in card["checklist"])
        assert any("乙酸" in i for i in items)
        assert "6M≠18M" in details or "6M" in details
        assert any("溶解" in i for i in items)
        assert any("苯胺" in i for i in items)
        assert any("密封" in i for i in items)

    def test_fluorinated_hint(self):
        card = generate_plan_card(A2_SMILES, TAPT_SMILES)
        assert any("氟" in h and "溶解" in h for h in card["monomer_hints"])

    def test_hydrazide_hint_and_model_warning(self):
        card = generate_plan_card(HYDRAZIDE, PA)
        hint = next((h for h in card["monomer_hints"] if "酰肼" in h), None)
        assert hint is not None
        assert "腙键" in hint
        assert "模型不适用" in hint

    def test_large_aromatic_hint(self):
        card = generate_plan_card(A2_SMILES, TAPT_SMILES)  # TAPT 四个芳环
        assert any("更长反应时间" in h for h in card["monomer_hints"])

    def test_small_monomers_no_large_aromatic_hint(self):
        card = generate_plan_card("O=CC=O", "NCCN")
        assert not any("更长反应时间" in h for h in card["monomer_hints"])

    def test_invalid_smiles_still_returns_card(self):
        card = generate_plan_card("junk_(((", "also junk")
        assert card["monomer_hints"] == []
        assert card["conditions"]["vessel"] == "35 mL Pyrex 管"
        assert card["aldehyde"]["smiles"] == "junk_((("

    def test_builtin_backfill(self):
        card = generate_plan_card(A2_SMILES, TAPT_SMILES)
        assert card["aldehyde"]["name"] == "A2"
        assert card["amine"]["cas"] == "14544-47-9"
