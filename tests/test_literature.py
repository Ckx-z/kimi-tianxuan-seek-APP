"""文献体系测试：resolver 解析 / 收藏响应 enrichment / Crossref lookup 草稿
（mock）/ confirm 入库与 409 重复 / 打分链路读 paper_titles.json 兼容性。

所有写操作打到 tmp_path（monkeypatch titles.TITLES_PATH / resolver.INTAKE_PATH /
fav_store.FAVORITES_DIR），不污染真实数据目录。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from favorites import store as fav_store  # noqa: E402
from literature import crossref, resolver  # noqa: E402
from references import titles  # noqa: E402

from api.main import app  # noqa: E402

client = TestClient(app)

TP = "O=CC1=C(C=O)C(=O)C(C=O)=C1O"
PA = "Nc1ccc(N)cc1"

MINI_LIB = {
    "1": {"doi": "10.1021/abc", "title": "Paper One"},
    "5": {"doi": "", "title": "Paper Five No Doi"},
}


@pytest.fixture()
def mini_lib(tmp_path, monkeypatch):
    """迷你文献库 + 独立审计流水；用完清 titles 缓存还原。

    防御：references.titles 可能以裸名与 src.* 两种形式各被 import 一次
    （双实例），两个实例的 TITLES_PATH 与缓存都要隔离，否则写操作会
    泄漏到真实 data/paper_titles.json。
    """
    p = tmp_path / "paper_titles.json"
    p.write_text(json.dumps(MINI_LIB, ensure_ascii=False), encoding="utf-8")
    patched = []
    for mod_name in ("references.titles", "src.references.titles"):
        mod = sys.modules.get(mod_name)
        if mod is not None and mod not in patched:
            monkeypatch.setattr(mod, "TITLES_PATH", p)
            mod.reload()
            patched.append(mod)
    monkeypatch.setattr(resolver, "INTAKE_PATH", tmp_path / "literature_intake.jsonl")
    yield p
    for mod in patched:
        mod.reload()


@pytest.fixture()
def fav_dir(tmp_path, monkeypatch):
    d = tmp_path / "favorites"
    monkeypatch.setattr(fav_store, "FAVORITES_DIR", d)
    return d


def _write_fav(d: Path, fav: dict) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{fav['id']}.json").write_text(
        json.dumps(fav, ensure_ascii=False), encoding="utf-8")


def _legacy_fav() -> dict:
    return {
        "id": "fav_20260101_001",
        "folder_id": "folder_default",
        "aldehyde": {"smiles": TP, "cas": "", "name": ""},
        "amine": {"smiles": PA, "cas": "", "name": ""},
        "created_at": "2026-01-01T10:00:00+08:00",
        "notes": "",
        "latest_prediction": None,
        "references": [
            {"title": "1", "doi": "", "source": "auto-matched",
             "path_or_url": "", "match_type": "both", "count": 3,
             "note": "报道过该醛胺组合"},
            {"title": "5", "doi": "", "source": "auto-matched",
             "path_or_url": "", "match_type": "aldehyde", "count": 1,
             "note": "报道过该醛单体"},
            {"title": "手动加的文献", "doi": "10.9999/zz", "source": "user-added",
             "path_or_url": "", "note": "支撑"},
        ],
        "experiment_record_ids": [],
    }


# ---------------------------------------------------------------- resolver

class TestResolver:
    def test_resolve_paper_with_doi(self, mini_lib):
        p = resolver.resolve_paper("1")
        assert p == {
            "paper_id": "1",
            "title": "Paper One",
            "doi": "10.1021/abc",
            "url": "https://doi.org/10.1021/abc",
            "has_doi": True,
        }

    def test_resolve_paper_without_doi(self, mini_lib):
        p = resolver.resolve_paper("5")
        assert p["has_doi"] is False
        assert p["doi"] == ""
        assert p["url"] is None
        assert p["title"] == "Paper Five No Doi"

    def test_resolve_paper_missing(self, mini_lib):
        assert resolver.resolve_paper("999") is None
        assert resolver.resolve_paper(None) is None
        assert resolver.resolve_paper("") is None

    def test_normalize_doi(self):
        assert resolver.normalize_doi(" 10.1021/ABC ") == "10.1021/ABC"
        assert resolver.normalize_doi("https://doi.org/10.1021/abc") == "10.1021/abc"
        assert resolver.normalize_doi("http://dx.doi.org/10.1/x") == "10.1/x"
        assert resolver.normalize_doi("") == ""
        assert resolver.normalize_doi(None) == ""

    def test_find_by_doi_case_and_prefix_insensitive(self, mini_lib):
        assert resolver.find_by_doi("10.1021/ABC")[0] == "1"
        assert resolver.find_by_doi("https://doi.org/10.1021/abc")[0] == "1"
        assert resolver.find_by_doi("10.0000/nope") is None
        assert resolver.find_by_doi("") is None

    def test_enrich_reference_legacy_numeric_title(self, mini_lib):
        ref = {"title": "1", "doi": "", "source": "auto-matched", "note": "x"}
        out = resolver.enrich_reference(ref)
        assert out["paper_id"] == "1"
        assert out["title"] == "Paper One"
        assert out["doi"] == "10.1021/abc"
        assert out["url"] == "https://doi.org/10.1021/abc"
        assert ref["title"] == "1"  # 入参不被修改

    def test_enrich_reference_no_doi_paper(self, mini_lib):
        out = resolver.enrich_reference({"title": "5", "doi": ""})
        assert out["title"] == "Paper Five No Doi"
        assert out["doi"] == ""
        assert out["url"] is None

    def test_enrich_reference_user_added_untouched(self, mini_lib):
        ref = {"title": "手动加的文献", "doi": "10.9999/zz", "source": "user-added"}
        out = resolver.enrich_reference(ref)
        assert out == ref
        assert "paper_id" not in out

    def test_enrich_reference_unresolvable_id(self, mini_lib):
        out = resolver.enrich_reference({"title": "999", "doi": ""})
        assert out["paper_id"] == "999"
        assert out["title"] == "999"  # 解析不到保留编号
        assert out["url"] is None


# ---------------------------------------------------------------- 收藏响应 enrichment

class TestFavoritesEnrichment:
    def test_get_favorite_enriches_legacy_refs(self, mini_lib, fav_dir):
        _write_fav(fav_dir, _legacy_fav())
        fav = fav_store.get_favorite("fav_20260101_001")
        refs = fav["references"]
        # 编号引用 → 真实标题/DOI/URL
        assert refs[0]["title"] == "Paper One"
        assert refs[0]["doi"] == "10.1021/abc"
        assert refs[0]["url"] == "https://doi.org/10.1021/abc"
        assert refs[0]["paper_id"] == "1"
        # 无 DOI 文献 → url 为 None，标题照常解析
        assert refs[1]["title"] == "Paper Five No Doi"
        assert refs[1]["url"] is None
        # 手动引用原样不动
        assert refs[2]["title"] == "手动加的文献"
        assert refs[2]["doi"] == "10.9999/zz"

    def test_disk_file_not_modified(self, mini_lib, fav_dir):
        _write_fav(fav_dir, _legacy_fav())
        fav_store.get_favorite("fav_20260101_001")
        saved = json.loads(
            (fav_dir / "fav_20260101_001.json").read_text(encoding="utf-8"))
        # 内存视图不落盘：磁盘上 references 仍是旧编号格式
        assert saved["references"][0]["title"] == "1"
        assert saved["references"][0]["doi"] == ""

    def test_list_favorites_enriched(self, mini_lib, fav_dir):
        _write_fav(fav_dir, _legacy_fav())
        favs = fav_store.list_favorites()
        assert favs[0]["references"][0]["title"] == "Paper One"

    def test_auto_match_writes_resolved_refs(self, mini_lib, fav_dir, monkeypatch):
        csv = fav_dir.parent / "mini_train.csv"
        csv.write_text(
            "paper_id,aldehyde_smiles,amine_smiles\n"
            f"1,{TP},{PA}\n"
            f"5,{TP},Nc1ccccc1\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(fav_store, "TRAIN_CSV", csv)
        refs = fav_store.auto_match_references(TP, PA)
        by_pid = {r["paper_id"]: r for r in refs}
        assert by_pid["1"]["title"] == "Paper One"
        assert by_pid["1"]["doi"] == "10.1021/abc"
        assert by_pid["1"]["url"] == "https://doi.org/10.1021/abc"
        assert by_pid["5"]["title"] == "Paper Five No Doi"
        assert by_pid["5"]["url"] is None
        for r in refs:
            assert r["source"] == "auto-matched"
            assert "path_or_url" in r and "match_type" in r and "count" in r


# ---------------------------------------------------------------- Crossref 草稿解析（纯函数）

class TestCrossrefDraft:
    def test_work_to_draft_full(self):
        work = {
            "title": ["COF <sub>2</sub> Film"],
            "author": [{"given": "Alice", "family": "Wang"},
                       {"given": "Bob", "family": "Li"}, {}],
            "container-title": ["JACS"],
            "issued": {"date-parts": [[2021, 5, 1]]},
            "DOI": "10.1021/jacs.1c00001",
            "abstract": "<jats:p>We report ...</jats:p>",
        }
        d = crossref.work_to_draft(work)
        assert d["title"] == "COF 2 Film"
        assert d["authors"] == ["Alice Wang", "Bob Li"]
        assert d["journal"] == "JACS"
        assert d["year"] == 2021
        assert d["doi"] == "10.1021/jacs.1c00001"
        assert d["url"] == "https://doi.org/10.1021/jacs.1c00001"
        assert d["abstract"] == "We report ..."
        assert d["source"] == "crossref"

    def test_work_to_draft_sparse(self):
        d = crossref.work_to_draft({})
        assert d["title"] == "" and d["authors"] == [] and d["year"] is None
        assert d["doi"] == "" and d["url"] is None and d["abstract"] is None

    def test_network_error_is_clear(self, monkeypatch):
        import requests

        def _boom(*a, **k):
            raise requests.ConnectionError("no route")

        monkeypatch.setattr(crossref.requests, "get", _boom)
        with pytest.raises(crossref.CrossrefError, match="无法连接 Crossref"):
            crossref.lookup_doi("10.1021/abc")

    def test_not_found(self, monkeypatch):
        class _Resp:
            status_code = 404

        monkeypatch.setattr(crossref.requests, "get", lambda *a, **k: _Resp())
        with pytest.raises(crossref.CrossrefNotFound):
            crossref.lookup_doi("10.1021/nope")


# ---------------------------------------------------------------- lookup 端点（mock crossref）

def _draft(doi="10.5555/new", title="New Paper"):
    return {
        "title": title,
        "authors": ["A Wang"],
        "journal": "JACS",
        "year": 2022,
        "doi": doi,
        "url": f"https://doi.org/{doi}",
        "abstract": None,
        "source": "crossref",
    }


class TestLookupApi:
    def test_lookup_by_doi_new(self, mini_lib, monkeypatch):
        monkeypatch.setattr(crossref, "lookup_doi", lambda doi: _draft())
        r = client.post("/api/literature/lookup", json={"doi": "10.5555/new"})
        assert r.status_code == 200
        d = r.json()["draft"]
        assert d["title"] == "New Paper"
        assert d["existing"] is False
        assert d["source"] == "crossref"

    def test_lookup_by_doi_existing(self, mini_lib, monkeypatch):
        monkeypatch.setattr(crossref, "lookup_doi",
                            lambda doi: _draft(doi="10.1021/abc", title="Paper One"))
        r = client.post("/api/literature/lookup", json={"doi": "10.1021/abc"})
        d = r.json()["draft"]
        assert d["existing"] is True
        assert d["existing_paper_id"] == "1"

    def test_lookup_by_title_three_candidates(self, mini_lib, monkeypatch):
        monkeypatch.setattr(
            crossref, "search_by_title",
            lambda title, rows=3: [_draft(doi=f"10.5555/{i}") for i in range(rows)])
        r = client.post("/api/literature/lookup", json={"title": "COF film"})
        assert r.status_code == 200
        cands = r.json()["candidates"]
        assert len(cands) == 3
        assert all(c["existing"] is False for c in cands)

    def test_lookup_requires_exactly_one(self, mini_lib):
        assert client.post("/api/literature/lookup", json={}).status_code == 400
        assert client.post("/api/literature/lookup",
                           json={"doi": "10.1/x", "title": "t"}).status_code == 400

    def test_lookup_crossref_down_502(self, mini_lib, monkeypatch):
        def _boom(doi):
            raise crossref.CrossrefError("无法连接 Crossref（Timeout）：请检查网络后重试")

        monkeypatch.setattr(crossref, "lookup_doi", _boom)
        r = client.post("/api/literature/lookup", json={"doi": "10.1/x"})
        assert r.status_code == 502
        assert "无法连接 Crossref" in r.json()["detail"]

    def test_lookup_doi_not_found_404(self, mini_lib, monkeypatch):
        def _boom(doi):
            raise crossref.CrossrefNotFound("Crossref 未找到该 DOI 对应的文献")

        monkeypatch.setattr(crossref, "lookup_doi", _boom)
        assert client.post("/api/literature/lookup",
                           json={"doi": "10.1/nope"}).status_code == 404


# ---------------------------------------------------------------- confirm 端点

class TestConfirmApi:
    def _payload(self, **kw):
        base = {
            "title": "用户审核后的标题",
            "authors": ["A Wang", "B Li"],
            "journal": "JACS",
            "year": 2023,
            "doi": "10.5555/brand-new",
            "abstract": "摘要",
            "source": "crossref",
            "reviewed_by": "ckx",
        }
        base.update(kw)
        return base

    def test_confirm_appends_library_and_audit(self, mini_lib):
        r = client.post("/api/literature/confirm", json=self._payload())
        assert r.status_code == 201
        body = r.json()
        assert body["paper_id"] == "6"  # 迷你库最大 id 5 → 新 id 6
        assert body["graphrag_indexed"] is False
        assert body["in_training"] is False
        assert body["audit_written"] is True
        assert body["url"] == "https://doi.org/10.5555/brand-new"
        # 落盘：新条目带完整字段
        lib = json.loads(mini_lib.read_text(encoding="utf-8"))
        entry = lib["6"]
        assert entry["doi"] == "10.5555/brand-new"
        assert entry["title"] == "用户审核后的标题"
        assert entry["authors"] == ["A Wang", "B Li"]
        assert entry["journal"] == "JACS" and entry["year"] == 2023
        assert entry["abstract"] == "摘要"
        assert entry["in_training"] is False
        assert entry["source"] == "user-intake"
        assert entry["added_at"]
        # 审计流水：原始草稿 + 最终稿 + 审核人
        lines = resolver.INTAKE_PATH.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        audit = json.loads(lines[0])
        assert audit["action"] == "confirm_intake"
        assert audit["paper_id"] == "6"
        assert audit["reviewed_by"] == "ckx"
        assert audit["draft"]["title"] == "用户审核后的标题"
        assert audit["final"]["doi"] == "10.5555/brand-new"
        assert audit["at"]

    def test_confirm_duplicate_doi_409(self, mini_lib):
        for doi in ("10.1021/abc", "10.1021/ABC", "https://doi.org/10.1021/abc"):
            r = client.post("/api/literature/confirm",
                            json=self._payload(doi=doi))
            assert r.status_code == 409, doi
            detail = r.json()["detail"]
            assert detail["existing_paper_id"] == "1"
        # 409 不落盘、不写审计
        lib = json.loads(mini_lib.read_text(encoding="utf-8"))
        assert set(lib) == {"1", "5"}
        assert not resolver.INTAKE_PATH.exists()

    def test_confirm_no_doi_allowed(self, mini_lib):
        r = client.post("/api/literature/confirm", json=self._payload(doi=""))
        assert r.status_code == 201
        assert r.json()["url"] is None

    def test_confirm_requires_title(self, mini_lib):
        r = client.post("/api/literature/confirm", json=self._payload(title="  "))
        assert r.status_code == 400


# ---------------------------------------------------------------- 打分链路兼容性

class TestScoringChainCompatibility:
    def test_append_does_not_break_existing_reads(self, mini_lib, monkeypatch):
        """confirm 追加新 key 后：既有条目读取不变、auto_match 照常工作。"""
        before = json.loads(mini_lib.read_text(encoding="utf-8"))
        r = client.post("/api/literature/confirm", json={
            "title": "兼容性验证文献", "doi": "10.5555/compat",
        })
        assert r.status_code == 201
        new_pid = r.json()["paper_id"]
        # 既有 key 原样保留（纯 dict 追加）
        after = json.loads(mini_lib.read_text(encoding="utf-8"))
        for pid, entry in before.items():
            assert after[pid] == entry
        assert set(after) == set(before) | {new_pid}
        # titles 读链路：旧条目照旧、新条目可解析
        assert titles.resolve_entry("1") == before["1"]
        assert titles.resolve_title(new_pid) == "兼容性验证文献"
        paper = resolver.resolve_paper(new_pid)
        assert paper["url"] == "https://doi.org/10.5555/compat"
        # auto_match（查询打分链路只读使用 paper_titles.json）对新旧 id 都能解析
        csv = mini_lib.parent / "compat_train.csv"
        csv.write_text(
            "paper_id,aldehyde_smiles,amine_smiles\n"
            f"1,{TP},{PA}\n"
            f"{new_pid},{TP},{PA}\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(fav_store, "TRAIN_CSV", csv)
        refs = fav_store.auto_match_references(TP, PA)
        by_pid = {r_["paper_id"]: r_ for r_ in refs}
        assert by_pid["1"]["title"] == "Paper One"
        assert by_pid[new_pid]["title"] == "兼容性验证文献"
        assert by_pid[new_pid]["url"] == "https://doi.org/10.5555/compat"

    def test_real_library_next_id_is_numeric_max_plus_one(self, mini_lib):
        assert resolver.next_paper_id() == "6"
