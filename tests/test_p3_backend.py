"""P3 后端模块测试：suggestions 读取 / 文献标题映射 / 游离实验记录。

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
from rag import suggestions as sug_store  # noqa: E402
from records import store as rec_store  # noqa: E402
from references import titles  # noqa: E402

TP = "O=CC1=C(C=O)C(=O)C(C=O)=C1O"
PA = "Nc1ccc(N)cc1"
# 内置库单体（用于游离记录 cas/name 反查）
A2_SMILES = "O=Cc1c(F)c(F)c(C=O)c(F)c1F"  # A2, cas 3217-47-8
TAPT_SMILES = "Nc1ccc(-c2nc(-c3ccc(N)cc3)nc(-c3ccc(N)cc3)n2)cc1"  # TAPT


def _sug(sug_id, favorite_id="fav_20260721_001", created_at="2026-07-21T22:05:00+08:00",
         sug_type="condition_adjust", status="new", schema_version="1.0",
         record_type="suggestion"):
    return {
        "schema_version": schema_version,
        "record_type": record_type,
        "suggestion_id": sug_id,
        "favorite_id": favorite_id,
        "type": sug_type,
        "payload": {"adjustments": []},
        "evidence_refs": [],
        "created_at": created_at,
        "status": status,
    }


def _write_sug(d: Path, name: str, obj) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


@pytest.fixture()
def sug_dir(tmp_path, monkeypatch):
    d = tmp_path / "suggestions"
    monkeypatch.setattr(sug_store, "SUGGESTIONS_DIR", d)
    return d


# ---------------------------------------------------------------- suggestions

class TestListSuggestions:
    def test_empty_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sug_store, "SUGGESTIONS_DIR", tmp_path / "nope")
        assert sug_store.list_suggestions() == []

    def test_list_and_sort_desc(self, sug_dir):
        _write_sug(sug_dir, "sug_20260720_001.json",
                   _sug("sug_20260720_001", created_at="2026-07-20T10:00:00+08:00"))
        _write_sug(sug_dir, "sug_20260721_002.json",
                   _sug("sug_20260721_002", created_at="2026-07-21T22:05:00+08:00"))
        _write_sug(sug_dir, "sug_20260721_001.json",
                   _sug("sug_20260721_001", created_at="2026-07-21T09:00:00+08:00"))
        ids = [s["suggestion_id"] for s in sug_store.list_suggestions()]
        assert ids == ["sug_20260721_002", "sug_20260721_001", "sug_20260720_001"]

    def test_filter_by_favorite_id(self, sug_dir):
        _write_sug(sug_dir, "sug_20260721_001.json",
                   _sug("sug_20260721_001", favorite_id="fav_20260721_001"))
        _write_sug(sug_dir, "sug_20260721_002.json",
                   _sug("sug_20260721_002", favorite_id="fav_20260721_002"))
        _write_sug(sug_dir, "sug_20260721_003.json",
                   _sug("sug_20260721_003", favorite_id=None))  # 通用建议
        got = sug_store.list_suggestions(favorite_id="fav_20260721_001")
        assert [s["suggestion_id"] for s in got] == ["sug_20260721_001"]

    def test_example_json_not_listed(self, sug_dir):
        _write_sug(sug_dir, "example.json", _sug("sug_20260721_001"))
        assert sug_store.list_suggestions() == []

    def test_corrupt_and_invalid_files_skipped(self, sug_dir):
        _write_sug(sug_dir, "sug_20260721_001.json", _sug("sug_20260721_001"))
        sug_dir.joinpath("sug_20260721_002.json").write_text("{损坏", encoding="utf-8")
        _write_sug(sug_dir, "sug_20260721_003.json", ["不是", "dict"])
        _write_sug(sug_dir, "sug_20260721_004.json",
                   _sug("sug_20260721_004", schema_version="2.0"))  # 未知版本跳过
        _write_sug(sug_dir, "sug_20260721_005.json",
                   _sug("sug_20260721_005", record_type="prediction"))
        bad = _sug("sug_20260721_006")
        bad["payload"] = "not-a-dict"
        _write_sug(sug_dir, "sug_20260721_006.json", bad)
        ids = [s["suggestion_id"] for s in sug_store.list_suggestions()]
        assert ids == ["sug_20260721_001"]

    def test_get_suggestion(self, sug_dir):
        _write_sug(sug_dir, "sug_20260721_001.json", _sug("sug_20260721_001"))
        got = sug_store.get_suggestion("sug_20260721_001")
        assert got["suggestion_id"] == "sug_20260721_001"
        assert sug_store.get_suggestion("sug_20990101_999") is None
        assert sug_store.get_suggestion("bad") is None
        assert sug_store.get_suggestion("") is None


# ---------------------------------------------------------------- 文献标题映射

@pytest.fixture()
def titles_file(tmp_path, monkeypatch):
    p = tmp_path / "paper_titles.json"
    p.write_text(
        json.dumps({"101": {"title": "COF film paper", "doi": "10.1000/x"},
                    "202": {"title": "", "doi": "10.1000/y"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(titles, "TITLES_PATH", p)
    titles.reload()
    yield p
    titles.reload()


class TestResolveTitle:
    def test_real_mapping_table(self):
        """入库的真实映射表可用（抽查构建来源里的已知条目）。"""
        titles.reload()
        entry = titles.resolve_entry("1")
        assert entry is not None
        assert "Triazine" in entry["title"]
        assert entry["doi"] == "10.1021/acs.langmuir.3c02095"
        e2 = titles.resolve_entry("1000")
        assert e2 is not None and e2["doi"] == "10.1016/j.chempr.2024.09.006"
        titles.reload()

    def test_hit_and_miss(self, titles_file):
        assert titles.resolve_title("101") == "COF film paper"
        assert titles.resolve_title(101) == "COF film paper"  # int 也行
        assert titles.resolve_title("999") is None
        assert titles.resolve_title(None) is None
        assert titles.resolve_title("") is None

    def test_empty_title_returns_none(self, titles_file):
        assert titles.resolve_title("202") is None
        assert titles.resolve_entry("202") == {"title": "", "doi": "10.1000/y"}

    def test_missing_file_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(titles, "TITLES_PATH", tmp_path / "nope.json")
        titles.reload()
        assert titles.resolve_title("1") is None
        assert titles.resolve_entry("1") is None
        titles.reload()

    def test_corrupt_file_returns_none(self, tmp_path, monkeypatch):
        p = tmp_path / "bad.json"
        p.write_text("{损坏", encoding="utf-8")
        monkeypatch.setattr(titles, "TITLES_PATH", p)
        titles.reload()
        assert titles.resolve_title("1") is None
        titles.reload()


# ---------------------------------------------------------------- 游离实验记录

@pytest.fixture()
def rec_dirs(tmp_path, monkeypatch):
    fav_d = tmp_path / "favorites"
    rec_d = tmp_path / "records"
    monkeypatch.setattr(fav_store, "FAVORITES_DIR", fav_d)
    monkeypatch.setattr(rec_store, "RECORDS_DIR", rec_d)
    return fav_d, rec_d


class TestOrphanRecord:
    def test_orphan_schema(self, rec_dirs):
        _, rec_d = rec_dirs
        rec = rec_store.create_record(
            favorite_id=None,
            aldehyde_smiles=TP,
            amine_smiles=PA,
            conditions={"solvent": "甲苯", "temperature_c": 120},
            outcome="film",
            experiment_no="B2",
            strength="连续膜",
            notes="未关联收藏",
            operator="ckx",
        )
        assert rec["favorite_id"] is None
        # 单体对象按收藏夹同一规则保存规范化 SMILES
        assert rec["aldehyde"] == fav_store._monomer_obj(TP)
        assert rec["amine"] == fav_store._monomer_obj(PA)
        assert set(rec["aldehyde"]) == {"smiles", "cas", "name"}
        assert rec["prediction_snapshot"] is None
        assert rec["prediction_id"] is None
        assert rec["outcome"] == "film"
        assert rec["conditions"]["solvent"] == "甲苯"
        assert rec["conditions"]["catalyst"] == ""
        saved = json.loads(
            (rec_d / f"{rec['record_id']}.json").read_text(encoding="utf-8")
        )
        assert saved["record_id"] == rec["record_id"]
        assert saved["favorite_id"] is None

    def test_orphan_cas_name_backfilled(self, rec_dirs):
        rec = rec_store.create_record(
            favorite_id=None,
            aldehyde_smiles=A2_SMILES,
            amine_smiles=TAPT_SMILES,
            outcome="partial",
            experiment_no="B3",
        )
        assert rec["aldehyde"]["cas"] == "3217-47-8"
        assert rec["aldehyde"]["name"] == "A2"
        assert rec["amine"]["cas"] == "14544-47-9"
        assert rec["amine"]["name"] == "TAPT"

    def test_orphan_requires_smiles(self, rec_dirs):
        for ald, amine in (("", PA), (TP, ""), ("", ""), (None, PA), ("  ", PA)):
            with pytest.raises(ValueError):
                rec_store.create_record(
                    favorite_id=None, aldehyde_smiles=ald, amine_smiles=amine,
                    outcome="film", experiment_no="T1",
                )

    def test_orphan_invalid_outcome_raises(self, rec_dirs):
        with pytest.raises(ValueError):
            rec_store.create_record(
                favorite_id=None, aldehyde_smiles=TP, amine_smiles=PA,
                outcome="success", experiment_no="T1",
            )

    def test_orphan_not_backlinked(self, rec_dirs):
        fav_d, _ = rec_dirs
        fav = fav_store.add_favorite(TP, PA)
        rec_store.create_record(
            favorite_id=None, aldehyde_smiles=TP, amine_smiles=PA, outcome="film",
            experiment_no="T1",
        )
        assert fav_store.get_favorite(fav["id"])["experiment_record_ids"] == []

    def test_orphan_listed_and_filterable(self, rec_dirs):
        fav_d, _ = rec_dirs
        fav = fav_store.add_favorite(TP, PA)
        rec_store.create_record(fav["id"], {}, "film", experiment_no="T1")  # 旧位置调用
        rec_store.create_record(
            favorite_id=None, aldehyde_smiles=TP, amine_smiles=PA, outcome="failed",
            experiment_no="T1",
        )
        assert len(rec_store.list_records()) == 2
        linked = rec_store.list_records(favorite_id=fav["id"])
        assert len(linked) == 1 and linked[0]["favorite_id"] == fav["id"]


class TestLegacyPositionalCompat:
    """旧签名 create_record(fid, conditions, outcome, strength, notes, operator) 仍可用。"""

    def test_legacy_positional_full(self, rec_dirs):
        fav_d, _ = rec_dirs
        fav = fav_store.add_favorite(TP, PA)
        rec = rec_store.create_record(
            fav["id"], {"solvent": "甲苯"}, "film", "强度好", "备注", "ckx",
            experiment_no="G2-3",
        )
        assert rec["favorite_id"] == fav["id"]
        assert rec["outcome"] == "film"
        assert rec["conditions"]["solvent"] == "甲苯"
        assert rec["strength"] == "强度好"
        assert rec["notes"] == "实验编号：G2-3；备注"  # P4a：experiment_no 并入 notes 前缀
        assert rec["operator"] == "ckx"

    def test_legacy_positional_partial(self, rec_dirs):
        fav_d, _ = rec_dirs
        fav = fav_store.add_favorite(TP, PA)
        rec = rec_store.create_record(fav["id"], {}, "partial", experiment_no="T1")
        assert rec["outcome"] == "partial"
        assert rec["strength"] == "" and rec["operator"] == ""
        assert rec["notes"] == "实验编号：T1"  # P4a：experiment_no 并入 notes 前缀

    def test_legacy_invalid_outcome_raises(self, rec_dirs):
        fav_d, _ = rec_dirs
        fav = fav_store.add_favorite(TP, PA)
        for bad in ("success", "", None):
            with pytest.raises(ValueError):
                rec_store.create_record(fav["id"], {}, bad, experiment_no="T1")

    def test_legacy_missing_favorite_raises(self, rec_dirs):
        with pytest.raises(KeyError):
            rec_store.create_record("fav_20990101_999", {}, "film", experiment_no="T1")

    def test_new_keyword_style_for_linked(self, rec_dirs):
        fav_d, _ = rec_dirs
        fav = fav_store.add_favorite(TP, PA)
        rec = rec_store.create_record(
            favorite_id=fav["id"], conditions={"solvent": "BTF"}, outcome="film",
            experiment_no="T1",
        )
        assert rec["favorite_id"] == fav["id"]
        assert rec["aldehyde"]["smiles"] == fav["aldehyde"]["smiles"]
        assert rec["conditions"]["solvent"] == "BTF"
