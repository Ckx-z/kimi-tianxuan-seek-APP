"""DFT 方法引用注册表测试（v1.5.4 文献 DOI 需求）。

覆盖：注册表完整性（每条引用含 DOI 与可点击 URL）、方法 key → 引用映射、
预设别名映射、导出输入文件注释中的 DOI、GET /api/dft/citations 契约。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for p in (str(PROJECT_ROOT), str(PROJECT_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from api import deps  # noqa: E402
from api.main import app  # noqa: E402
from src.dft import citations, export as dft_export  # noqa: E402

client = TestClient(app)


class _FakePredictor:  # 与 test_api.py 同款桩，避免加载模型
    tree_available = True
    gnn_available = False
    router = None

    def predict(self, ald, amine):
        return {"tree_probability": 0.5, "tree_std": 0.0,
                "tree_model_name": "tree_v4", "tree_route": "both_seen",
                "ood": {"level": "none", "reasons": []}}


@pytest.fixture(autouse=True)
def fake_predictor(monkeypatch):
    monkeypatch.setattr(deps, "_PREDICTOR", _FakePredictor())
    monkeypatch.setattr("api.routers.predict.get_predictor",
                        lambda: deps._PREDICTOR)


# ---------------------------------------------------------------------------
# 注册表完整性
# ---------------------------------------------------------------------------

def test_registry_entries_have_doi_and_url():
    for group in (citations.METHOD_CITATIONS, citations.PRESET_CITATIONS):
        for key, refs in group.items():
            assert isinstance(refs, list), key
            for ref in refs:
                assert ref["doi"], f"{key}/{ref['key']} 缺 DOI"
                assert ref["url"] == f"https://doi.org/{ref['doi']}"
                assert ref["label"] and ref["cite"]


def test_key_dois_correct():
    """关键文献 DOI 逐一核对（防止登记笔误）。"""
    def find(key: str) -> dict:
        for refs in citations.METHOD_CITATIONS.values():
            for ref in refs:
                if ref["key"] == key:
                    return ref
        raise AssertionError(f"缺引用条目: {key}")

    assert find("liu2021")["doi"] == "10.1016/j.jhazmat.2020.123917"
    assert find("b3lyp-becke")["doi"] == "10.1063/1.464913"
    assert find("b3lyp-lyp")["doi"] == "10.1103/PhysRevB.37.785"
    assert find("wb97x-d")["doi"] == "10.1039/b810189b"
    assert find("d3")["doi"] == "10.1063/1.3382344"
    assert find("d3bj")["doi"] == "10.1002/jcc.21759"
    assert find("gfn2-xtb")["doi"] == "10.1021/acs.jctc.8b01176"
    assert find("gfn-ff")["doi"] == "10.1002/anie.202004239"
    assert find("xtb")["doi"] == "10.1002/wcms.1493"
    assert find("crest")["doi"] == "10.1039/c9cp06869d"
    assert find("etkdg")["doi"] == "10.1021/acs.jcim.5b00654"
    assert find("uff")["doi"] == "10.1021/ja00051a040"
    assert find("psi4")["doi"] == "10.1063/5.0006002"


def test_citations_for_mapping():
    # xTB 快速档
    keys = [r["key"] for r in citations.citations_for("gfn2", backend="xtb")]
    assert keys == ["gfn2-xtb", "xtb"]
    keys = [r["key"] for r in citations.citations_for("gfnff", backend="xtb")]
    assert keys == ["gfn-ff", "xtb"]
    # Psi4 文献口径：B3LYP + 刘璐 2021 + Psi4
    keys = [r["key"] for r in citations.citations_for(
        "b3lyp_631gdp", backend="psi4")]
    assert keys == ["b3lyp-becke", "b3lyp-lyp", "liu2021", "psi4"]
    # 采样引擎附加引用
    keys = [r["key"] for r in citations.citations_for(
        "gfn2", backend="xtb", sampling="crest")]
    assert keys == ["gfn2-xtb", "xtb", "crest"]
    # 未知方法：xTB 无兜底 → 空；Psi4 兜底 Psi4 程序引用
    assert citations.citations_for("nonexistent", backend="xtb") == []
    keys = [r["key"] for r in citations.citations_for(
        "nonexistent", backend="psi4")]
    assert keys == ["psi4"]
    # 无方法无后端 → 空
    assert citations.citations_for(None) == []


def test_citations_for_preset():
    keys = [r["key"] for r in citations.citations_for_preset("literature")]
    assert keys == ["b3lyp-becke", "b3lyp-lyp", "liu2021", "psi4"]
    keys = [r["key"] for r in citations.citations_for_preset("precision")]
    assert keys == ["wb97x-d", "d3", "d3bj", "psi4"]
    assert citations.citations_for_preset("nope") == []


# ---------------------------------------------------------------------------
# 导出输入文件注释含 DOI
# ---------------------------------------------------------------------------

def test_export_comments_include_doi():
    xyz = "3\n\nC 0.0 0.0 0.0\nC 0.0 0.0 1.5\nH 0.0 1.0 0.0\n"
    gjf = dft_export.build_gaussian_input(xyz)
    assert "DOI: 10.1063/1.464913" in gjf
    assert "DOI: 10.1016/j.jhazmat.2020.123917" in gjf
    assert "方法引用：" in gjf
    inp = dft_export.build_orca_input(xyz)
    assert "DOI: 10.1063/1.464913" in inp
    assert "# 方法引用：" in inp


# ---------------------------------------------------------------------------
# API 端点
# ---------------------------------------------------------------------------

def test_api_dft_citations():
    r = client.get("/api/dft/citations")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"methods", "presets"}
    # 方法 key 齐全
    for key in ("gfn2", "gfnff", "wb97xd3bj_svp", "b3lyp_631gdp"):
        assert key in body["methods"], key
        assert body["methods"][key][0]["doi"]
    # 文献口径 preset 含刘璐 2021 DOI
    lit_keys = [ref["key"] for ref in body["presets"]["literature"]]
    assert "liu2021" in lit_keys
    liu = next(ref for ref in body["presets"]["literature"]
               if ref["key"] == "liu2021")
    assert liu["doi"] == "10.1016/j.jhazmat.2020.123917"
    assert liu["url"] == "https://doi.org/10.1016/j.jhazmat.2020.123917"
