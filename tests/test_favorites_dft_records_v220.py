"""档案卡与 DFT 分条记录（v220）测试。

覆盖：
- 相同计算条件重复收藏 → 不新增（更新同一条）且标号不变；
- 任一条件不同 → 新增记录且标号递增（A→B→C）；
- 二聚体模式与 pair（任意双分子）模式都能收藏，卡类型可区分；
- 失败记录可删除且持久化（标号不重排）；
- 手工补充 CAS / 改名持久化，SMILES 等计算产物不可改；
- 旧数据（无 condition_key/label 的历史条目）读取不回炸，读取补派生标号；
- 端点级补齐：条目只带 job_id 时从 DFT 任务补齐条件字段（同任务重复入库不新增）。

数据目录一律 monkeypatch 到 tmp_path（绝不写仓库 data/ 或真实用户数据目录）。
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

from api.main import app  # noqa: E402

client = TestClient(app)

TP = "O=CC1=C(C=O)C(=O)C(C=O)=C1O"
PA = "Nc1ccc(N)cc1"

# 条件基线（二聚体模式 · GFN2-xTB · 自身堆积 · 12 采样）
BASE_CONDITIONS = {
    "mode": "dimer",
    "backend": "xtb",
    "method": "gfn2",
    "a_smiles": TP,
    "b_smiles": PA,
    "x_type": "self_stack",
    "n_samples": 12,
    "with_props": True,
}


@pytest.fixture()
def fav_dir(tmp_path, monkeypatch):
    """收藏目录隔离到 tmp_path（store 层按属性读取，patch 即生效）。"""
    d = tmp_path / "favorites"
    monkeypatch.setattr(fav_store, "FAVORITES_DIR", d)
    return d


def _create_favorite(**kw) -> dict:
    body = {"aldehyde_smiles": TP, "amine_smiles": PA}
    body.update(kw)
    r = client.post("/api/favorites", json=body)
    assert r.status_code == 201
    return r.json()


class TestPairFavoriteCreation:
    """v2.20 修复：**pair（任意双分子）结果也能收藏**，并成为双分子 DFT 特殊档案卡。

    背景（用户实测阻塞）：DFT 页此前对 pair 模式禁用收藏按钮 → 特殊档案卡无入口、
    新功能无法测试。修复后前端放开按钮，并在 dft_snapshot 里带上
    `mode` / `job_id` / `a_smiles` / `b_smiles` / `conditions`
    （缺 mode 会被判成二聚体卡；缺 job_id 则点记录跳不回该次结果）。

    本测试直接覆盖后端接受「带 pair 快照的收藏」这条路径（前端就是发这个体）。
    """

    def test_create_favorite_with_pair_snapshot(self, fav_dir):
        snap = {
            "job_id": "dft_pair_demo_1",
            "mode": "pair",
            "a_smiles": "CCO",
            "b_smiles": "Oc1ccccc1",
            "conditions": {"mode": "pair", "backend": "psi4",
                           "method": "wb97xd3bj_svp",
                           "a_smiles": "CCO", "b_smiles": "Oc1ccccc1"},
            "backend": "psi4", "method": "wb97xd3bj_svp",
            "e_bind_kcal": -4.88, "e_bind_kj": -20.42,
            "x_smiles": "Oc1ccccc1", "x_type": "custom",
        }
        fav = _create_favorite(aldehyde_smiles="CCO", amine_smiles="Oc1ccccc1",
                               dft_snapshot=snap)
        fid = fav["id"]

        body = client.get(f"/api/favorites/{fid}").json()
        # ① 判成「双分子 DFT 计算特殊档案卡」
        assert body["archive_type"] == "dft_pair", body.get("archive_type")
        # ② 旧快照被读成一条 DFT 记录（无需先发生写操作）
        entries = body["dft_entries"]
        assert len(entries) == 1
        entry = entries[0]
        # ③ mode 与 job_id 都被保住 —— 前者决定卡类型，后者决定"点击记录看该次结果"
        assert entry["mode"] == "pair"
        assert entry["job_id"] == "dft_pair_demo_1"
        assert entry["e_bind_kcal"] == -4.88
        # ④ 响应里带派生标号（该组第一条 → A）
        assert body["dft_labels"] == ["A"]
        # ⑤ 派生字段不落盘（与 bc442c5 的修复一致）
        disk = json.loads((fav_dir / f"{fid}.json").read_text(encoding="utf-8"))
        assert "dft_labels" not in disk and "archive_type" not in disk

    def test_pair_favorite_then_append_second_run(self, fav_dir):
        """同一组双分子再算一次（不同档位）→ 追加成第二条记录，标号 B。"""
        fid = _create_favorite(
            aldehyde_smiles="CCO", amine_smiles="Oc1ccccc1",
            dft_snapshot={"job_id": "run1", "mode": "pair", "method": "gfn2",
                          "echo": 1}).get("id")
        body = _post_entry(fid, {
            "job_id": "run2", "mode": "pair", "method": "wb97xd3bj_svp",
            "e_bind_kcal": -6.1,
            "conditions": {"mode": "pair", "backend": "psi4",
                           "method": "wb97xd3bj_svp",
                           "a_smiles": "CCO", "b_smiles": "Oc1ccccc1"},
        })
        assert body["archive_type"] == "dft_pair"
        assert len(body["dft_entries"]) == 2
        # 同组（同单体对 + 同目标分子）→ 续号 B
        assert body["dft_labels"] == ["A", "B"]


def _post_entry(fav_id: str, payload: dict) -> dict:
    r = client.post(f"/api/favorites/{fav_id}/dft-entries", json=payload)
    assert r.status_code == 200, r.text
    return r.json()


def _disk_entries(fav_dir: Path, fav_id: str) -> list[dict]:
    on_disk = json.loads((fav_dir / f"{fav_id}.json").read_text(encoding="utf-8"))
    return on_disk["dft_entries"]


def _labels(entries: list[dict]) -> list[str]:
    """条目自带 label（新写入口径）。"""
    return [e.get("label") for e in entries]


def _labels_of(fav: dict) -> list[str]:
    """标号显示口径（与前端 entryLabels 一致）：条目 label > 响应派生数组。"""
    entries = fav.get("dft_entries") or []
    derived = fav.get("dft_labels") or []
    out = []
    for i, e in enumerate(entries):
        own = e.get("label")
        out.append(own if isinstance(own, str) and own.strip()
                   else (derived[i] if i < len(derived) else None))
    return out


# ---------------------------------------------------------------- 条件指纹与标号


class TestConditionFingerprintAndLabels:
    def test_same_conditions_updates_same_record_and_keeps_label(self, fav_dir):
        """相同条件重复收藏 → 只一条记录（更新结果），标号不变。"""
        fid = _create_favorite()["id"]
        first = {"job_id": "job_a1", "e_bind_kcal": -3.20,
                 "e_bind_kj": -13.39, "conditions": dict(BASE_CONDITIONS)}
        body = _post_entry(fid, first)
        assert len(body["dft_entries"]) == 1
        entry = body["dft_entries"][0]
        assert entry["label"] == "A"
        key_a = entry["condition_key"]
        assert isinstance(key_a, str) and len(key_a) == 40  # sha1 十六进制摘要
        created_a = entry["created_at"]

        # 同条件（仅刷新结果）→ 不新增，标号/条件指纹/首次时间不变
        second = {"job_id": "job_a2", "e_bind_kcal": -3.55,
                  "e_bind_kj": -14.85, "conditions": dict(BASE_CONDITIONS)}
        body = _post_entry(fid, second)
        assert len(body["dft_entries"]) == 1
        entry = body["dft_entries"][0]
        assert entry["label"] == "A"
        assert entry["condition_key"] == key_a
        assert entry["job_id"] == "job_a2"          # 结果已更新
        assert entry["e_bind_kcal"] == pytest.approx(-3.55)
        assert entry["created_at"] == created_a      # 首次时间保留

        # 条件字段顺序变化不影响指纹（稳定序列化 + 排序）
        reordered = dict(reversed(list(BASE_CONDITIONS.items())))
        body = _post_entry(fid, {"job_id": "job_a3", "e_bind_kcal": -3.6,
                                 "conditions": reordered})
        assert len(body["dft_entries"]) == 1
        assert body["dft_entries"][0]["label"] == "A"

        # 磁盘持久化：只有一条
        assert len(_disk_entries(fav_dir, fid)) == 1

    def test_any_condition_change_adds_record_with_next_label(self, fav_dir):
        """任一条件不同（method/complex_xyz/n_samples/with_props/mode/X）→ 新增 A→B→C…"""
        fid = _create_favorite()["id"]
        _post_entry(fid, {"job_id": "j1", "e_bind_kcal": -3.2,
                          "conditions": dict(BASE_CONDITIONS)})

        variants = [
            # 计算档位不同
            {**BASE_CONDITIONS, "method": "gfnff"},
            # 初始构象（complex_xyz_hash）不同
            {**BASE_CONDITIONS, "complex_xyz_hash": "deadbeef"},
            # 采样数不同
            {**BASE_CONDITIONS, "n_samples": 24},
            # 是否算性质不同
            {**BASE_CONDITIONS, "with_props": False},
            # 目标分子（X）不同 —— 这一条属于**另一个标号分组**，要从 A 重新开始
            {**BASE_CONDITIONS, "x_type": "custom", "x_smiles": "c1ccccc1"},
        ]
        # v2.20 用户口径：标号按「单体组 + 目标分子」分组，每组独立从 A 开始。
        # 前 4 个变体只是"同一次计算的变体"（档位/构象/采样/性质）→ 同组，续 B、C、D、E；
        # 第 5 个换了目标分子 → 新组，回到 A。
        expect = [
            ["A", "B"],
            ["A", "B", "C"],
            ["A", "B", "C", "D"],
            ["A", "B", "C", "D", "E"],
            ["A", "B", "C", "D", "E", "A"],
        ]
        for i, cond in enumerate(variants):
            body = _post_entry(fid, {"job_id": f"j{i + 2}",
                                     "e_bind_kcal": -1.0 - i,
                                     "conditions": cond})
            assert _labels(body["dft_entries"]) == expect[i], (
                f"第 {i + 1} 个变体的标号应为 {expect[i]}，"
                f"实际 {_labels(body['dft_entries'])}")
        assert len(body["dft_entries"]) == 6
        assert len({e["condition_key"] for e in body["dft_entries"]}) == 6

        # 后端模式不同也算条件不同（dimer → pair）：mode 不参与标号分组 →
        # 与前面 4 个变体同组（同单体组 + 同目标分子），因此续在 E 之后取 F
        body = _post_entry(fid, {"job_id": "j_pair", "e_bind_kcal": -0.5,
                                 "conditions": {**BASE_CONDITIONS, "mode": "pair"}})
        assert _labels(body["dft_entries"]) == ["A", "B", "C", "D", "E", "A", "F"]

        # 标签超过 26 条走双字母（直接验证标号生成器口径）
        assert fav_store._label_at(0) == "A"
        assert fav_store._label_at(25) == "Z"
        assert fav_store._label_at(26) == "AA"
        assert fav_store._label_at(27) == "AB"

    def test_both_modes_collectable_and_archive_type(self, fav_dir):
        """二聚体模式与任意双分子（pair）模式都能收藏，档案卡类型可区分。"""
        fid = _create_favorite()["id"]
        body = _post_entry(fid, {
            "job_id": "p1", "mode": "pair", "e_bind_kcal": -1.2, "e_bind_kj": -5.02,
            "conditions": {"mode": "pair", "backend": "xtb", "method": "gfn2",
                           "a_smiles": "CCO", "b_smiles": "O"},
        })
        assert body["archive_type"] == "dft_pair"
        assert body["dft_entries"][0]["mode"] == "pair"

        # pair 模式换方法档位 → 新增一条（同一张卡保留多条记录）
        body = _post_entry(fid, {
            "job_id": "p2", "mode": "pair", "e_bind_kcal": -1.0,
            "conditions": {"mode": "pair", "backend": "xtb", "method": "gfnff",
                           "a_smiles": "CCO", "b_smiles": "O"},
        })
        assert len(body["dft_entries"]) == 2
        assert _labels(body["dft_entries"]) == ["A", "B"]
        assert body["archive_type"] == "dft_pair"

        # 混入一条二聚体记录：v2.20 用户口径「含任一 pair 记录即为双分子特殊卡」
        # → 仍判 dft_pair（旧 all 规则下会回落成 cof_monomer_group，已按用户决策改）
        body = _post_entry(fid, {"job_id": "d1", "e_bind_kcal": -4.0,
                                 "conditions": dict(BASE_CONDITIONS)})
        assert len(body["dft_entries"]) == 3
        assert body["archive_type"] == "dft_pair"
        # GET 与列表接口同样带出档案卡类型（前端据此选卡片样式）
        assert client.get(f"/api/favorites/{fid}").json()["archive_type"] == "dft_pair"


# ---------------------------------------------------------------- 失败记录删除


class TestDeleteDftRecord:
    def _fav_with_two(self) -> tuple[str, list[dict]]:
        fid = _create_favorite()["id"]
        _post_entry(fid, {"job_id": "ok1", "e_bind_kcal": -3.0,
                          "conditions": dict(BASE_CONDITIONS)})
        body = _post_entry(fid, {"job_id": "bad1",
                                 "error": "xTB 退出码 1：几何优化不收敛",
                                 "conditions": {**BASE_CONDITIONS, "method": "gfnff"}})
        return fid, body["dft_entries"]

    def test_failed_record_deletable_and_persisted(self, fav_dir):
        fid, entries = self._fav_with_two()
        assert len(entries) == 2 and entries[1]["label"] == "B"
        assert entries[1]["error"]

        r = client.delete(f"/api/favorites/{fid}/dft-entries/1")
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body["dft_entries"]) == 1
        assert body["dft_entries"][0]["job_id"] == "ok1"
        assert body["deleted_entry"]["job_id"] == "bad1"
        assert body["deleted_index"] == 1
        # GET 响应 dft_snapshot 同步为剩下的那条
        got = client.get(f"/api/favorites/{fid}").json()
        assert got["dft_snapshot"]["job_id"] == "ok1"
        # 落盘持久化（重新读文件仍在）
        assert [e["job_id"] for e in _disk_entries(fav_dir, fid)] == ["ok1"]

    def test_labels_not_renumbered_after_delete(self, fav_dir):
        """删除后已有标号不重排：新增记录继续用下一个未占用字母。"""
        fid = _create_favorite()["id"]
        for i, cond in enumerate([
            dict(BASE_CONDITIONS),
            {**BASE_CONDITIONS, "method": "gfnff"},
            {**BASE_CONDITIONS, "n_samples": 6},
        ]):
            _post_entry(fid, {"job_id": f"j{i}", "e_bind_kcal": -1.0 * i,
                              "conditions": cond})
        # 删中间那条（B）→ A、C 保持原标号
        r = client.delete(f"/api/favorites/{fid}/dft-entries/1")
        assert r.status_code == 200
        assert _labels(r.json()["dft_entries"]) == ["A", "C"]
        # 新增（第 4 个条件）→ 取 D，不复用被删的 B
        body = _post_entry(fid, {"job_id": "j9", "e_bind_kcal": -9.0,
                                 "conditions": {**BASE_CONDITIONS, "with_props": False}})
        assert _labels(body["dft_entries"]) == ["A", "C", "D"]

    def test_delete_missing_targets_404(self, fav_dir):
        fid, _ = self._fav_with_two()
        assert client.delete(f"/api/favorites/{fid}/dft-entries/9").status_code == 404
        assert client.delete(
            "/api/favorites/fav_20990101_999/dft-entries/0").status_code == 404


# ---------------------------------------------------------------- 单体元数据编辑


class TestMonomerMetaEdit:
    def test_rename_and_manual_cas_persisted(self, fav_dir):
        fav = _create_favorite(ald_name="Tp", amine_name="Pa")
        fid = fav["id"]
        smiles_on_disk = json.loads(
            (fav_dir / f"{fid}.json").read_text(encoding="utf-8"))["aldehyde"]["smiles"]

        r = client.patch(f"/api/favorites/{fid}/monomer-meta",
                         json={"aldehyde": {"name": "TFPT（三醛基间苯三酚）",
                                            "cas": "3435-24-5"},
                               "amine": {"cas": "106-50-3"}})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["aldehyde"]["name"] == "TFPT（三醛基间苯三酚）"
        assert body["aldehyde"]["cas"] == "3435-24-5"
        assert body["amine"]["cas"] == "106-50-3"
        assert body["amine"]["name"] == "Pa"          # 未提供的字段不动
        # SMILES 等计算产物不被改写
        assert body["aldehyde"]["smiles"] == smiles_on_disk

        # 持久化：重新读取（GET + 磁盘）一致
        got = client.get(f"/api/favorites/{fid}").json()
        assert got["aldehyde"]["cas"] == "3435-24-5"
        on_disk = json.loads((fav_dir / f"{fid}.json").read_text(encoding="utf-8"))
        assert on_disk["aldehyde"]["name"] == "TFPT（三醛基间苯三酚）"
        assert on_disk["amine"]["cas"] == "106-50-3"

        # 全角连字符 CAS 归一为半角
        r = client.patch(f"/api/favorites/{fid}/monomer-meta",
                         json={"amine": {"cas": "106－50－3"}})
        assert r.json()["amine"]["cas"] == "106-50-3"

    def test_meta_edit_rejects_bad_input(self, fav_dir):
        fid = _create_favorite()["id"]
        # 空补丁（未提供任何可改字段）
        assert client.patch(f"/api/favorites/{fid}/monomer-meta",
                            json={}).status_code == 400
        assert client.patch(f"/api/favorites/{fid}/monomer-meta",
                            json={"aldehyde": {}}).status_code == 400
        # 不存在的收藏
        assert client.patch("/api/favorites/fav_20990101_999/monomer-meta",
                            json={"aldehyde": {"name": "x"}}).status_code == 404
        # 超长名称被 schema 拦下（422）
        assert client.patch(f"/api/favorites/{fid}/monomer-meta",
                            json={"aldehyde": {"name": "x" * 300}}).status_code == 422


# ---------------------------------------------------------------- 旧数据兼容


class TestLegacyCompatibility:
    def _write_legacy(self, fav_dir: Path, fav_id: str, entries=None,
                      snapshot=None) -> str:
        """写一条旧格式收藏：无 condition_key/label（可选无 dft_entries）。"""
        fav_dir.mkdir(parents=True, exist_ok=True)
        legacy = {
            "id": fav_id,
            "folder_id": "folder_default",
            "aldehyde": {"smiles": TP, "cas": "", "name": "Tp"},
            "amine": {"smiles": PA, "cas": "", "name": "Pa"},
            "created_at": "2020-01-01T00:00:00+08:00",
            "notes": "",
            "latest_prediction": None,
            "dft_snapshot": snapshot,
            "references": [],
            "experiment_record_ids": [],
        }
        if entries is not None:
            legacy["dft_entries"] = entries
        (fav_dir / f"{fav_id}.json").write_text(
            json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
        return fav_id

    def test_legacy_entries_without_key_or_label_read_ok(self, fav_dir):
        """无 condition_key/label 的历史条目：读取不炸，读取期补派生标号。"""
        legacy_entries = [
            {"job_id": "old1", "method": "gfn2", "x_type": "self_stack",
             "e_bind_kcal": -2.0},
            {"job_id": "old2", "method": "gfn2", "x_type": "self_stack",
             "e_bind_kcal": -2.5},
        ]
        fid = self._write_legacy(fav_dir, "fav_20191231_001", entries=legacy_entries)

        fav = client.get(f"/api/favorites/{fid}").json()
        assert _labels_of(fav) == ["A", "B"]      # 读取期派生标号
        # 历史条目本身在响应里保持原样（不塞 condition_key/label）
        assert fav["dft_entries"][0] == legacy_entries[0]
        # 旧前端字段照旧可用
        assert fav["dft_snapshot"]["job_id"] == "old2"
        assert fav["archive_type"] == "cof_monomer_group"
        # 派生结果与顺序稳定（重复读取一致），且不因读取改写磁盘
        again = client.get(f"/api/favorites/{fid}").json()
        assert _labels_of(again) == ["A", "B"]
        on_disk = json.loads((fav_dir / f"{fid}.json").read_text(encoding="utf-8"))
        assert on_disk["dft_entries"] == legacy_entries

        # 新条件 → 新增一条：该条带完整条件（已知分组），而历史条目没有条件信息
        # 无法归组 —— 卡里此时只有一个已知分组，历史条目按"通配"并入同一条序列，
        # 因此新记录顺延取 C，而不是另起一个 A 造成同卡重复标号。
        body = _post_entry(fid, {"job_id": "new1", "e_bind_kcal": -1.5,
                                 "conditions": dict(BASE_CONDITIONS)})
        labels = _labels_of(body)
        assert len(labels) == 3
        assert labels[-1] == "C"
        assert len(set(labels)) == 3
        # 写路径固化：全部条目带标号，新写入的条目带条件指纹（旧条目保持原样，
        # 不因读取被塞派生字段）
        disk = _disk_entries(fav_dir, fid)
        assert all(e.get("label") for e in disk)
        written = next(e for e in disk if e.get("job_id") == "new1")
        assert written["condition_key"]

    def test_legacy_single_snapshot_migrates_and_reads(self, fav_dir):
        """更老的数据：只有 dft_snapshot、无 dft_entries → 读取期包成一条记录。"""
        fid = self._write_legacy(
            fav_dir, "fav_20191231_002", entries=None,
            snapshot={"job_id": "ancient", "method": "gfnff", "e_bind_kcal": -1.1})
        fav = client.get(f"/api/favorites/{fid}").json()
        assert len(fav["dft_entries"]) == 1
        assert fav["dft_entries"][0]["job_id"] == "ancient"
        assert _labels_of(fav) == ["A"]
        assert fav["dft_snapshot"]["job_id"] == "ancient"
        # 读取不改写磁盘（旧快照仍留在 dft_snapshot 字段里）
        on_disk = json.loads((fav_dir / f"{fid}.json").read_text(encoding="utf-8"))
        assert on_disk["dft_entries"] == []
        assert on_disk["dft_snapshot"]["job_id"] == "ancient"

        # 首次写入：旧快照搬进 dft_entries（新结构落盘），标号 A 固化
        body = _post_entry(fid, {"job_id": "fresh", "e_bind_kcal": -2.0,
                                 "conditions": dict(BASE_CONDITIONS)})
        assert [e["job_id"] for e in body["dft_entries"]] == ["ancient", "fresh"]
        assert _labels_of(body) == ["A", "B"]


# ---------------------------------------------------------------- 端点级条件补齐


class TestConditionEnrichmentFromJob:
    def test_entry_with_only_job_id_gets_conditions_from_job(self, fav_dir, monkeypatch):
        """老客户端只传 job_id：后端从 DFT 任务补齐条件字段（含 xyz 指纹）。

        同一任务重复入库 → 指纹相同 → 不新增（防止"同一次计算记成两条"）。
        """
        try:
            from src.dft import jobs as dft_jobs
        except ImportError:  # pragma: no cover
            from dft import jobs as dft_jobs  # type: ignore

        job = dft_jobs.create_job(
            TP, PA, "gfn2", x_type="self_stack", backend="xtb",
            n_samples=24, complex_xyz="3\nwater\nO 0.0 0.0 0.0\nH 0.0 0.0 1.0\nH 0.1 0.0 1.1\n")
        fid = _create_favorite()["id"]
        body = _post_entry(fid, {"job_id": job["job_id"], "e_bind_kcal": -2.0})
        entry = body["dft_entries"][0]
        assert entry["label"] == "A"
        cond = entry["conditions"]
        assert cond["method"] == "gfn2"
        assert cond["backend"] == "xtb"
        assert cond["n_samples"] == 24
        assert len(cond["complex_xyz_hash"]) == 40

        # 同一任务再来一次（只改结果）→ 更新同一条
        body = _post_entry(fid, {"job_id": job["job_id"], "e_bind_kcal": -2.8})
        assert len(body["dft_entries"]) == 1
        assert body["dft_entries"][0]["label"] == "A"
        assert body["dft_entries"][0]["e_bind_kcal"] == pytest.approx(-2.8)

        # 同单体同方法但换采样数 → 条件不同 → 新增 B
        job2 = dft_jobs.create_job(TP, PA, "gfn2", x_type="self_stack",
                                   backend="xtb", n_samples=6)
        body = _post_entry(fid, {"job_id": job2["job_id"], "e_bind_kcal": -2.1})
        assert _labels(body["dft_entries"]) == ["A", "B"]

    def test_unknown_job_id_still_saves(self, fav_dir):
        """job_id 查不到（服务重启/测试假 id）不阻塞保存，仅条件字段少一些。"""
        fid = _create_favorite()["id"]
        body = _post_entry(fid, {"job_id": "no_such_job", "method": "gfn2",
                                 "mode": "dimer", "e_bind_kcal": -1.7})
        assert len(body["dft_entries"]) == 1
        assert body["dft_entries"][0]["condition_key"]
