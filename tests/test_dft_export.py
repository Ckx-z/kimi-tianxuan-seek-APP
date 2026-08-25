"""P4 测试：DFT 导出（gjf/inp）+ 迭代上下文 DFT 数据注入。

- 导出纯函数：gjf 含 b3lyp 路由行且坐标数与 xyz 原子数一致、以空行结尾；
  inp 含主输入行与坐标块；原子数不一致 / 未知格式 → 中文错误
- 导出端点：200 下载（中文文件名 RFC 5987 编码）、400 未知格式、
  404 未完成/不存在任务
- 迭代上下文：收藏快照命中 / DFT 缓存命中 / 缓存未命中三条路径；
  build_messages 注入与引用纪律；normalize_evidence 的 dft_data 白名单
"""

from __future__ import annotations

import importlib.util
import sys
import time
import urllib.parse
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for p in (str(PROJECT_ROOT), str(PROJECT_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.dft import cache as dft_cache  # noqa: E402
from src.dft import engine  # noqa: E402
from src.dft import export as dft_export  # noqa: E402
from src.dft import log as dft_log  # noqa: E402
from favorites import store as fav_store  # noqa: E402

XYZ = "3\ncomplex\nC 0 0 0\nN 1.4 0 0\nO 2.5 0 0\n"

FAKE_RESULT = {
    "smiles_a": engine.canonicalize_smiles("O=CC1=C(C=O)C(=O)C(C=O)=C1O"),
    "smiles_b": engine.canonicalize_smiles("Nc1ccc(N)cc1"),
    "dimer_smiles": "Nc1ccc(N=Cc2ccc(C=O)cc2)cc1",
    "dimer_multi_site": True,
    "dimer_note": "示意单点缩合：多位点单体仅缩合第一个位点",
    "x_type": "self_stack",
    "x_smiles": "Nc1ccc(N=Cc2ccc(C=O)cc2)cc1",
    "x_description": "自身堆积（二聚体·二聚体）",
    "x_cache_part": "self_stack",
    "x_request": {"solvent_id": None, "ald2_smiles": None,
                  "amine2_smiles": None, "custom_smiles": None},
    "method": "gfn2",
    "method_label": "GFN2-xTB（精确）",
    "e_bind_hartree": -0.012,
    "e_bind_kcal": -7.5301,
    "e_bind_kj": -31.506,
    "energies_hartree": {"dimer": -100.0, "x": -50.0, "complex": -150.012},
    "gap_ev": {"dimer": 5.0, "x": 6.1, "complex": 4.2},
    "dipole_debye": {"dimer": 0.1, "x": 1.5, "complex": 1.2},
    "complex_xyz": XYZ,
    "elapsed_sec": 0.01,
}


# ---------------------------------------------------------------- 导出纯函数

class TestExportBuilders:
    def test_gaussian_route_and_coords(self):
        gjf = dft_export.build_gaussian_input(XYZ, source="gfn2")
        assert "%nprocshared=" in gjf
        assert "%mem=" in gjf
        assert "# opt b3lyp/6-31g(d) scrf=smd" in gjf
        # 注释说明需自行检查自旋/电荷
        assert "自旋多重度" in gjf
        # 电荷/自旋多重度行 + 坐标数与 xyz 原子数一致
        assert "\n0 1\n" in gjf
        coord_lines = [ln for ln in gjf.splitlines()
                       if ln and ln.split()[0] in ("C", "N", "O")
                       and len(ln.split()) == 4]
        assert len(coord_lines) == 3
        assert gjf.endswith("\n") or gjf.splitlines()[-1] == ""  # 空行结尾

    def test_orca_coord_block(self):
        inp = dft_export.build_orca_input(XYZ, source="gfn2")
        assert "! B3LYP def2-SVP OPT" in inp
        assert "* xyz 0 1" in inp
        block = inp.split("* xyz 0 1", 1)[1]
        coords = [ln for ln in block.splitlines()
                  if ln.strip() and ln.strip() != "*"]
        assert len(coords) == 3
        assert block.rstrip().endswith("*")
        assert "自旋多重度" in inp  # # 注释行说明

    def test_atom_count_mismatch_raises(self):
        with pytest.raises(dft_export.DftExportError, match="不一致"):
            dft_export.parse_xyz_coords("4\ncomplex\nC 0 0 0\n")

    def test_bad_xyz_raises(self):
        with pytest.raises(dft_export.DftExportError):
            dft_export.parse_xyz_coords("not-a-number\ncomment\n")

    def test_unknown_format_raises(self):
        with pytest.raises(dft_export.DftExportError, match="未知导出格式"):
            dft_export.build_export("gamess", XYZ)

    def test_filename_chinese(self):
        name = dft_export.export_filename("gaussian", "gfn2")
        assert name.endswith(".gjf")
        assert "gaussian" in name
        assert dft_export.export_filename("orca", "gfn2").endswith(".inp")


# ---------------------------------------------------------------- 导出端点

@pytest.fixture()
def client():
    from api.main import app
    return TestClient(app)


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """隔离缓存/历史/收藏目录 + 秒回假引擎（与 test_dft_api 口径一致）。"""
    monkeypatch.setattr(dft_cache, "CACHE_DIR", tmp_path / "dft_cache")
    monkeypatch.setattr(dft_log, "LOG_PATH", tmp_path / "dft_log.jsonl")
    monkeypatch.setattr(fav_store, "FAVORITES_DIR", tmp_path / "favorites")
    monkeypatch.setattr(engine, "xtb_binary", lambda: tmp_path / "xtb.exe")

    def _fake_compute(smiles_a, smiles_b, method="gfn2", on_stage=None,
                      jobs_root=None, **_k):
        result = dict(FAKE_RESULT)
        result["method"] = method
        return result

    monkeypatch.setattr(engine, "compute_binding", _fake_compute)


def _make_done_job(client) -> str:
    r = client.post("/api/dft/jobs", json={
        "smiles_a": "O=CC1=C(C=O)C(=O)C(C=O)=C1O",
        "smiles_b": "Nc1ccc(N)cc1", "method": "gfn2"})
    assert r.status_code == 202
    job_id = r.json()["job_id"]
    deadline = time.time() + 10
    while time.time() < deadline:
        body = client.get(f"/api/dft/jobs/{job_id}").json()
        if body["status"] in ("done", "failed"):
            assert body["status"] == "done"
            return job_id
        time.sleep(0.05)
    raise AssertionError("任务未完成")


class TestExportEndpoint:
    def test_gaussian_download(self, client, sandbox):
        job_id = _make_done_job(client)
        r = client.get(f"/api/dft/jobs/{job_id}/export?format=gaussian")
        assert r.status_code == 200
        assert "b3lyp/6-31g(d) scrf=smd" in r.text
        assert "C 0 0 0" in r.text
        cd = r.headers.get("content-disposition", "")
        assert "attachment" in cd
        # 中文文件名经 RFC 5987 编码，解码后可还原
        star = [seg for seg in cd.split(";") if "filename*=" in seg]
        assert star, f"缺少 filename* 段: {cd}"
        encoded = star[0].split("''", 1)[1]
        assert urllib.parse.unquote(encoded).endswith(".gjf")

    def test_orca_download(self, client, sandbox):
        job_id = _make_done_job(client)
        r = client.get(f"/api/dft/jobs/{job_id}/export?format=orca")
        assert r.status_code == 200
        assert "! B3LYP def2-SVP OPT" in r.text
        assert "* xyz 0 1" in r.text

    def test_unknown_format_400(self, client, sandbox):
        job_id = _make_done_job(client)
        r = client.get(f"/api/dft/jobs/{job_id}/export?format=gamess")
        assert r.status_code == 400
        assert "未知导出格式" in r.json()["detail"]

    def test_unknown_job_404(self, client, sandbox):
        assert client.get("/api/dft/jobs/no-such/export").status_code == 404

    def test_export_404_before_done(self, client, sandbox, monkeypatch):
        import threading

        def _slow(*_a, **_k):
            threading.Event().wait(0.5)
            return dict(FAKE_RESULT)
        monkeypatch.setattr(engine, "compute_binding", _slow)
        r = client.post("/api/dft/jobs", json={
            "smiles_a": "O=CC=O", "smiles_b": "Nc1ccccc1", "method": "gfn2"})
        job_id = r.json()["job_id"]
        assert client.get(
            f"/api/dft/jobs/{job_id}/export?format=gaussian").status_code == 404


# ---------------------------------------------------------------- 迭代上下文 DFT 注入

def _load_iterate_module():
    """按 api/routers/iterate.py 同款方式加载编排器模块。"""
    spec = importlib.util.spec_from_file_location(
        "cof_iterate_suggest_test",
        PROJECT_ROOT / "minimax" / "adapters" / "iterate_suggest.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def it():
    return _load_iterate_module()


class TestIterateDftContext:
    SNAPSHOT = {
        "method": "gfn2",
        "e_bind_kcal": -5.2,
        "e_bind_kj": -21.76,
        "gap_ev": {"a": 5.0, "b": 6.1, "complex": 4.2},
        "dipole_debye": {"a": 0.1, "b": 1.5, "complex": 1.2},
        "date": "2026-07-22T10:00:00+08:00",
    }

    def test_favorite_snapshot_hit(self, it):
        """收藏带 dft_snapshot：注入文本含结合能，引用标记 dft:gfn2。"""
        favorite = {"dft_snapshot": dict(self.SNAPSHOT)}
        text, ref = it.lookup_dft_context(favorite=favorite)
        assert ref == "dft:gfn2"
        assert "-5.20 kcal/mol" in text
        assert "GFN2-xTB" in text
        assert "4.20 eV" in text
        assert "1.20 Debye" in text
        assert "dft:gfn2" in text

    def test_cache_hit_path(self, it, tmp_path, monkeypatch):
        """无收藏快照时查 DFT 缓存（2.0 口径：二聚体+自身堆积 key）：命中返回注入文本。"""
        from src.dft import dimer as dimer_mod
        monkeypatch.setattr(dft_cache, "CACHE_DIR", tmp_path)
        dim = dimer_mod.make_dimer("O=CC=O", "Nc1ccccc1")
        key = dft_cache.cache_key(dim["smiles"], "self_stack", "gfn2")
        dft_cache.save_cache(key, dict(FAKE_RESULT))
        text, ref = it.lookup_dft_context(
            aldehyde={"smiles": "O=CC=O"}, amine={"smiles": "Nc1ccccc1"},
            favorite=None)
        assert ref == "dft:gfn2"
        assert "-7.53 kcal/mol" in text
        assert "缓存" in text

    def test_cache_miss_degrades_silently(self, it, tmp_path, monkeypatch):
        """缓存未命中：静默返回 (None, None)，不抛异常。"""
        monkeypatch.setattr(dft_cache, "CACHE_DIR", tmp_path)
        text, ref = it.lookup_dft_context(
            aldehyde={"smiles": "O=CC=O"}, amine={"smiles": "Nc1ccccc1"},
            favorite=None)
        assert (text, ref) == (None, None)

    def test_no_smiles_no_snapshot(self, it):
        """既无快照又无 SMILES：静默 (None, None)。"""
        assert it.lookup_dft_context(favorite=None) == (None, None)
        assert it.lookup_dft_context(favorite={}) == (None, None)

    def test_build_messages_injects_dft(self, it):
        """有 DFT 上下文：user prompt 含「DFT 计算数据」段，sys prompt 放开 dft_data 引用。"""
        msgs = it.build_messages("结合能怎么看", {}, {}, [], "(证据)", [],
                                 dft_context="结合能 -5.20 kcal/mol（测试）")
        sys_prompt, user = msgs[0]["content"], msgs[1]["content"]
        assert "DFT 计算数据" in user
        assert "-5.20" in user
        assert "dft_data" in sys_prompt
        assert "引用标记" in sys_prompt

    def test_build_messages_without_dft(self, it):
        """无 DFT 上下文：prompt 不出现 DFT 段（行为与注入前一致）。"""
        msgs = it.build_messages("怎么调", {}, {}, [], "(证据)", [])
        assert "DFT 计算数据" not in msgs[1]["content"]
        assert "引用标记" not in msgs[0]["content"]

    def test_dft_ref_whitelist(self, it):
        """注入 DFT 后 dft_data 引用合法且计为有效证据。"""
        item = {"evidence_refs": [
            {"kind": "dft_data", "ref": "dft:gfn2", "note": "结合能依据"}]}
        refs, unverified, n_valid = it.normalize_evidence(
            item, [], [], dft_ref="dft:gfn2")
        assert n_valid == 1
        assert refs[0]["kind"] == "dft_data"
        assert refs[0]["ref"] == "dft:gfn2"
        assert unverified == []

    def test_dft_ref_rejected_when_not_injected(self, it):
        """未注入 DFT 数据时的 dft_data 引用被剔除进 unverified_refs。"""
        item = {"evidence_refs": [
            {"kind": "dft_data", "ref": "dft:gfn2", "note": "编造引用"}]}
        refs, unverified, n_valid = it.normalize_evidence(item, [], [])
        assert n_valid == 0
        assert all(r["kind"] != "dft_data" for r in refs)
        assert unverified and unverified[0]["ref"] == "dft:gfn2"

    def test_snapshot_invalid_shape_ignored(self, it):
        """dft_snapshot 缺 e_bind_kcal 数值时视为无数据（不注入）。"""
        text, ref = it.lookup_dft_context(
            favorite={"dft_snapshot": {"method": "gfn2"}})
        assert (text, ref) == (None, None)

    def test_snapshot_dimer_wording(self, it):
        """2.0 快照（含 dimer_smiles/x_description）：注入文案为二聚体+X 口径。"""
        snap = dict(self.SNAPSHOT)
        snap["dimer_smiles"] = "Nc1ccc(N=Cc2ccc(C=O)cc2)cc1"
        snap["x_type"] = "self_stack"
        snap["x_description"] = "自身堆积（二聚体·二聚体）"
        text, ref = it.lookup_dft_context(favorite={"dft_snapshot": snap})
        assert ref == "dft:gfn2"
        assert "缩合二聚体" in text
        assert "自身堆积（二聚体·二聚体）" in text
        assert "缩合二聚体与 X 的结合能：-5.20 kcal/mol" in text

    def test_snapshot_legacy_wording(self, it):
        """旧版 v1.0.0 快照（无 x_description）：降级标注旧口径，不臆造 X 描述。"""
        text, ref = it.lookup_dft_context(
            favorite={"dft_snapshot": dict(self.SNAPSHOT)})
        assert ref == "dft:gfn2"
        assert "缩合二聚体" in text
        assert "旧版记录未保存 X 描述" in text
        assert "两单体结合能口径" in text

    def test_cache_hit_dimer_wording(self, it, tmp_path, monkeypatch):
        """2.0 缓存命中：注入文案带 X 描述（自身堆积）。"""
        from src.dft import dimer as dimer_mod
        monkeypatch.setattr(dft_cache, "CACHE_DIR", tmp_path)
        dim = dimer_mod.make_dimer("O=CC=O", "Nc1ccccc1")
        key = dft_cache.cache_key(dim["smiles"], "self_stack", "gfn2")
        dft_cache.save_cache(key, dict(FAKE_RESULT))
        text, ref = it.lookup_dft_context(
            aldehyde={"smiles": "O=CC=O"}, amine={"smiles": "Nc1ccccc1"},
            favorite=None)
        assert ref == "dft:gfn2"
        assert "缩合二聚体与 X（自身堆积（二聚体·二聚体））" in text
