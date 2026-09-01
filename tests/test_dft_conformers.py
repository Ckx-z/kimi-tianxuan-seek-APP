"""构象采样模块测试（v1.5.0）：ETKDG 生成/排序、CREST 降级、手动摆放端点。

不依赖真实 xtb：单点能量用 monkeypatch 伪造（按 xyz 哈希制造能量差异）。
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

from src.dft import conformers as dft_conformers  # noqa: E402
from src.dft import engine  # noqa: E402

FLEX = "CCCC(=O)OC"  # 丁酸甲酯：有可旋转键


@pytest.fixture()
def fake_xtb_sp(monkeypatch):
    """伪造 xTB 单点：能量随 xyz 内容变化（不同构象得到不同相对能量）。"""
    monkeypatch.setattr(engine, "xtb_binary", lambda: "fake-xtb.exe")
    calls = []

    def _fake_sp(xyz_block, args, cwd, timeout, opt=False):
        calls.append(xyz_block)
        e = -40.0 - 0.002 * (hash(xyz_block.splitlines()[2]) % 50)
        return (f"| TOTAL ENERGY {e:.10f} Eh   |\n normal termination of xtb",
                None)
    monkeypatch.setattr(engine, "_run_xtb", _fake_sp)
    return calls


class TestEtkdg:
    def test_generates_sorted_with_window(self, fake_xtb_sp):
        confs = dft_conformers.generate_conformers_etkdg(
            FLEX, n_gen=12, max_confs=6, e_window_kj=50.0)
        assert 0 < len(confs) <= 6
        rels = [c["rel_e_kj"] for c in confs]
        assert rels == sorted(rels)
        assert rels[0] == 0.0
        # 输出结构完整
        for c in confs:
            assert c["xyz"].strip().splitlines()[0].isdigit()
            assert c["rel_e_kcal"] == pytest.approx(c["rel_e_kj"] / 4.184, abs=0.01)

    def test_window_filters(self, fake_xtb_sp):
        confs = dft_conformers.generate_conformers_etkdg(
            FLEX, n_gen=12, max_confs=20, e_window_kj=1.0)
        assert all(c["rel_e_kj"] <= 1.0 for c in confs)

    def test_invalid_smiles_empty(self):
        assert dft_conformers.generate_conformers_etkdg("not-a-smiles") == []

    def test_no_xtb_falls_back_to_mmff(self, monkeypatch):
        monkeypatch.setattr(engine, "xtb_binary", lambda: None)
        confs = dft_conformers.generate_conformers_etkdg(
            FLEX, n_gen=10, max_confs=5)
        assert len(confs) >= 1  # MMFF 回退路径仍能产出构象


class TestCrest:
    def test_crest_missing_returns_empty(self, monkeypatch, tmp_path):
        monkeypatch.setattr(dft_conformers, "crest_binary", lambda: None)
        assert dft_conformers.generate_conformers_crest("3\nx\nC 0 0 0\nH 1 0 0\nH 0 1 0\n") == []

    def test_engines_report(self):
        engines = dft_conformers.conformer_engines()
        assert engines["etkdg"]["installed"] is True
        assert "crest" in engines


class TestConformerEndpoints:
    @pytest.fixture()
    def client(self):
        from api.main import app
        return TestClient(app)

    def test_engines_endpoint(self, client):
        r = client.get("/api/dft/conformers/engines")
        assert r.status_code == 200
        assert "etkdg" in r.json()["engines"]

    def test_generate_endpoint_with_fake(self, client, monkeypatch):
        monkeypatch.setattr(
            dft_conformers, "generate_conformers",
            lambda smiles, engine_name, n_gen=50, max_confs=20,
            e_window_kj=10.0, timeout=3600: [
                {"id": "fake-00", "xyz": "1\nx\nC 0 0 0\n",
                 "rel_e_kj": 0.0, "rel_e_kcal": 0.0, "boltzmann_w": 1.0}])
        r = client.post("/api/dft/conformers/generate",
                        json={"smiles": FLEX, "engine": "etkdg"})
        assert r.status_code == 200
        body = r.json()
        assert len(body["conformers"]) == 1
        assert body["engine"] == "etkdg"

    def test_generate_invalid_smiles_400(self, client):
        r = client.post("/api/dft/conformers/generate",
                        json={"smiles": "xx!!", "engine": "etkdg"})
        assert r.status_code == 400

    def test_generate_unknown_engine_400(self, client):
        r = client.post("/api/dft/conformers/generate",
                        json={"smiles": FLEX, "engine": "magic"})
        assert r.status_code == 400

    def test_manual_endpoint(self, client):
        r = client.post("/api/dft/conformers/manual", json={
            "a_smiles": "c1ccccc1", "b_smiles": "Oc1ccccc1",
            "tx": 0.0, "ty": 0.0, "tz": 3.0,
            "rx_deg": 0.0, "ry_deg": 0.0, "rz_deg": 0.0})
        assert r.status_code == 200
        body = r.json()
        lines = body["xyz"].strip().splitlines()
        assert int(lines[0]) == 25  # 苯 12 + 苯酚 13
        assert body["atom_budget"] == {"a": 12, "b": 13, "complex": 25}
        assert body["fragment_ranges"] == {"a": [0, 12], "b": [12, 25]}

    def test_manual_invalid_smiles_400(self, client):
        r = client.post("/api/dft/conformers/manual", json={
            "a_smiles": "bad!", "b_smiles": "Oc1ccccc1"})
        assert r.status_code == 400
