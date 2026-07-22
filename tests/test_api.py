"""FastAPI 地基测试：路由连通 + 契约形状（真实后端，不重跑模型重活）。

预测类接口用 monkeypatch 替换 get_predictor，避免加载模型；
数据类接口（favorites/records）打到临时目录，不碰真实数据。
"""

import pytest
from fastapi.testclient import TestClient

from api import deps
from api.main import app

client = TestClient(app)


class _FakePredictor:
    tree_available = True
    gnn_available = False
    router = None

    def predict(self, ald, amine):
        return {
            "tree_probability": 0.65, "tree_std": 0.04,
            "tree_model_name": "tree_v4", "tree_route": "both_seen",
            "ood": {"level": "none", "reasons": []},
        }


@pytest.fixture(autouse=True)
def fake_predictor(monkeypatch):
    monkeypatch.setattr(deps, "_PREDICTOR", _FakePredictor())
    monkeypatch.setattr("api.routers.predict.get_predictor",
                        lambda: deps._PREDICTOR)


# ---------------------------------------------------------------------------
# 基础
# ---------------------------------------------------------------------------

def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["tree_available"] is True


def test_openapi_lists_core_routes():
    paths = client.get("/openapi.json").json()["paths"]
    for p in ("/api/predict", "/api/predict/batch", "/api/favorites",
              "/api/records", "/api/monomers", "/api/monomers/props",
              "/api/plan-templates", "/api/plan-card", "/api/llm/settings",
              "/api/llm/test"):
        assert p in paths, p


# ---------------------------------------------------------------------------
# 打分
# ---------------------------------------------------------------------------

def test_predict_single_contract():
    r = client.post("/api/predict",
                    json={"ald_smiles": "O=Cc1ccccc1",
                          "amine_smiles": "Nc1ccccc1"})
    assert r.status_code == 200
    d = r.json()
    assert d["score"] == 0.65
    assert d["score_policy"] == "max_tree_gnn"
    assert d["score_source"] == "tree"      # 仅树出分
    assert d["ood"]["level"] == "none"


def test_predict_ood_out_nulls_score(monkeypatch):
    class _Ood(_FakePredictor):
        def predict(self, a, b):
            r = super().predict(a, b)
            r["ood"] = {"level": "out", "reasons": ["非标准官能团"]}
            return r
    monkeypatch.setattr(deps, "_PREDICTOR", _Ood())
    r = client.post("/api/predict",
                    json={"ald_smiles": "X", "amine_smiles": "Y"})
    assert r.json()["score"] is None        # ⛔ 优先于打分


def test_predict_ood_out_nulls_components(monkeypatch):
    """OOD=out 时 tree/gnn 分量与 std 同步置 None（防止绕过 score 读分量）。"""
    class _Ood(_FakePredictor):
        def predict(self, a, b):
            r = super().predict(a, b)
            r["gnn_probability"] = 0.7
            r["gnn_std"] = 0.05
            r["ood"] = {"level": "out", "reasons": ["非标准官能团"]}
            return r
    monkeypatch.setattr(deps, "_PREDICTOR", _Ood())
    r = client.post("/api/predict",
                    json={"ald_smiles": "X", "amine_smiles": "Y"})
    d = r.json()
    assert d["score"] is None
    for k in ("tree_score", "gnn_score", "tree_std", "gnn_std"):
        assert d[k] is None, k


def test_predict_empty_400():
    r = client.post("/api/predict", json={"ald_smiles": "", "amine_smiles": "N"})
    assert r.status_code == 400


def test_predict_batch_sorted():
    class _Var(_FakePredictor):
        def predict(self, a, b):
            r = super().predict(a, b)
            r["tree_probability"] = 0.9 if "F" in a else 0.3
            return r
    import api.routers.predict as rp
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(deps, "_PREDICTOR", _Var())
    monkeypatch.setattr(rp, "get_predictor", lambda: deps._PREDICTOR)
    r = client.post("/api/predict/batch", json={"pairs": [
        {"ald_smiles": "O=Cc1ccccc1", "amine_smiles": "Nc1ccccc1"},
        {"ald_smiles": "O=Cc1cc(F)cc(F)c1", "amine_smiles": "Nc1ccccc1"},
    ]})
    scores = [x["score"] for x in r.json()["results"]]
    assert scores == sorted(scores, reverse=True)
    monkeypatch.undo()


# ---------------------------------------------------------------------------
# 收藏 / 记录（临时目录隔离）
# ---------------------------------------------------------------------------

@pytest.fixture
def isolated_data(tmp_path, monkeypatch):
    from favorites import store as fav_store
    from records import store as rec_store
    monkeypatch.setattr(fav_store, "FAVORITES_DIR", tmp_path / "favs")
    monkeypatch.setattr(rec_store, "RECORDS_DIR", tmp_path / "recs")
    return fav_store, rec_store


def test_favorites_crud(isolated_data):
    r = client.post("/api/favorites", json={
        "aldehyde_smiles": "O=Cc1ccccc1", "amine_smiles": "Nc1ccccc1"})
    assert r.status_code == 201
    fid = r.json()["id"]
    assert client.get("/api/favorites").json()["favorites"]
    assert client.get(f"/api/favorites/{fid}").status_code == 200
    assert client.delete(f"/api/favorites/{fid}").json()["deleted"] == fid
    assert client.get(f"/api/favorites/{fid}").status_code == 404


def test_records_crud_and_validation(isolated_data):
    # 缺实验编号 → 400
    r = client.post("/api/records", json={
        "aldehyde_smiles": "O=Cc1ccccc1", "amine_smiles": "Nc1ccccc1",
        "outcome": "film", "experiment_no": ""})
    assert r.status_code == 400
    # 游离记录完整创建 → 读取 → 删除
    r = client.post("/api/records", json={
        "aldehyde_smiles": "O=Cc1ccccc1", "amine_smiles": "Nc1ccccc1",
        "conditions": {"solvent_1": "甲苯"}, "outcome": "film",
        "experiment_no": "API-1"})
    assert r.status_code == 201
    rid = r.json()["record_id"]
    assert client.get(f"/api/records/{rid}").json()["experiment_no"] == "API-1"
    assert client.delete(f"/api/records/{rid}").status_code == 200
    assert client.get(f"/api/records/{rid}").status_code == 404


def test_records_duplicate_experiment_no_warning(isolated_data):
    """同 favorite 下重复 experiment_no：返回警告字段但不拦截保存。"""
    r = client.post("/api/favorites", json={
        "aldehyde_smiles": "O=Cc1ccccc1", "amine_smiles": "Nc1ccccc1"})
    assert r.status_code == 201
    fid = r.json()["id"]
    body = {"favorite_id": fid, "outcome": "film", "experiment_no": "A5"}
    r1 = client.post("/api/records", json=body)
    assert r1.status_code == 201
    assert "duplicate_experiment_no" not in r1.json()   # 首次创建无警告
    r2 = client.post("/api/records", json=body)
    assert r2.status_code == 201                        # 不拦截保存
    assert r2.json()["duplicate_experiment_no"] is True
    assert r2.json()["record_id"] != r1.json()["record_id"]


# ---------------------------------------------------------------------------
# 单体 / 模板 / LLM
# ---------------------------------------------------------------------------

def test_monomers_library():
    r = client.get("/api/monomers")
    assert r.status_code == 200
    d = r.json()
    assert d["aldehydes"] and d["amines"]


def test_monomer_props_rdkit_only():
    r = client.get("/api/monomers/props",
                   params={"smiles": "Nc1ccccc1", "name": "苯胺"})
    assert r.status_code == 200
    assert r.json()["facts"]["mw"] > 90


def test_monomer_props_invalid_smiles_400_no_llm(monkeypatch):
    """非法 SMILES → 400，且在 RDKit 校验阶段拦截，不烧 LLM。"""
    import recommend.monomer_props as mp

    def _boom(*a, **k):  # 若走到性质卡/LLM 流程则直接失败
        raise AssertionError("非法 SMILES 不应进入 get_monomer_properties")

    monkeypatch.setattr(mp, "get_monomer_properties", _boom)
    r = client.get("/api/monomers/props", params={"smiles": "not_a_smiles!!"})
    assert r.status_code == 400


def test_plan_templates_builtin():
    r = client.get("/api/plan-templates")
    assert r.status_code == 200
    ids = [t.get("id") for t in r.json()["templates"]]
    assert any("hou" in str(i) or "builtin" in str(i) for i in ids)


def test_plan_card_default_template():
    r = client.post("/api/plan-card", json={
        "aldehyde_smiles": "O=Cc1ccccc1", "amine_smiles": "Nc1ccccc1"})
    assert r.status_code == 200
    assert r.json()["steps"]


def test_llm_settings_mask():
    r = client.get("/api/llm/settings")
    assert r.status_code == 200
    d = r.json()
    assert "api_key" not in d               # 绝不回显原文
    assert "api_key_masked" in d


# ---------------------------------------------------------------------------
# 页⑤ 迭代路由 + 性质卡批量
# ---------------------------------------------------------------------------

def _write_suggestion(directory, **kw):
    """造一条建议 JSON 到隔离目录，返回 dict。"""
    import json as _json
    sug = {
        "schema_version": "1.0", "record_type": "suggestion",
        "suggestion_id": kw.get("suggestion_id", "sug_20260722_001"),
        "favorite_id": kw.get("favorite_id"),
        "type": "condition_adjust",
        "payload": {"title": "调溶剂", "adjustments": [{"note": "甲苯加量"}]},
        "evidence_refs": [],
        "created_at": kw.get("created_at", "2026-07-22T14:00:00+08:00"),
        "status": kw.get("status", "pending"),
    }
    if "batch" in kw:
        sug["batch"] = kw["batch"]
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{sug['suggestion_id']}.json").write_text(
        _json.dumps(sug, ensure_ascii=False), encoding="utf-8")
    return sug


@pytest.fixture
def isolated_iterate(tmp_path, monkeypatch):
    """页⑤数据隔离：建议/方案/收藏全部打到临时目录。"""
    import api.routers.iterate as it
    from src.recommend import generated_plans as gp
    sugs, plans, favs = tmp_path / "sugs", tmp_path / "plans", tmp_path / "favs"
    for m in (it, gp):
        monkeypatch.setattr(m, "SUGGESTIONS_DIR", sugs)
        monkeypatch.setattr(m, "PLANS_DIR", plans)
    # 收藏目录只在 generated_plans 侧（iterate 路由不直接读收藏）
    monkeypatch.setattr(gp, "FAVORITES_DIR", favs)
    return sugs, plans, favs


def test_iterate_suggestions_list_and_filter(isolated_iterate):
    sugs, _, _ = isolated_iterate
    _write_suggestion(sugs, suggestion_id="sug_20260722_001",
                      favorite_id="fav_a", status="pending",
                      batch="batch_20260722_100000",
                      created_at="2026-07-22T10:00:00+08:00")
    _write_suggestion(sugs, suggestion_id="sug_20260722_002",
                      favorite_id="fav_b", status="adopted",
                      created_at="2026-07-22T15:00:00+08:00")
    # 全量：created_at 倒序
    r = client.get("/api/iterate/suggestions")
    assert r.status_code == 200
    ids = [s["suggestion_id"] for s in r.json()["suggestions"]]
    assert ids == ["sug_20260722_002", "sug_20260722_001"]
    # 过滤
    r = client.get("/api/iterate/suggestions",
                   params={"favorite_id": "fav_a"})
    assert [s["suggestion_id"] for s in r.json()["suggestions"]] == [
        "sug_20260722_001"]
    r = client.get("/api/iterate/suggestions", params={"status": "adopted"})
    assert [s["suggestion_id"] for s in r.json()["suggestions"]] == [
        "sug_20260722_002"]
    r = client.get("/api/iterate/suggestions",
                   params={"batch": "batch_20260722_100000"})
    assert r.json()["count"] == 1


def test_iterate_suggestions_corrupt_file_skipped(isolated_iterate):
    sugs, _, _ = isolated_iterate
    _write_suggestion(sugs, suggestion_id="sug_20260722_001")
    sugs.mkdir(parents=True, exist_ok=True)
    (sugs / "sug_20260722_999.json").write_text("{损坏", encoding="utf-8")
    r = client.get("/api/iterate/suggestions")
    assert r.status_code == 200
    assert r.json()["count"] == 1   # 损坏文件跳过，不影响整体


def test_iterate_suggest_success(monkeypatch):
    """suggest 成功：subprocess 返回契约末行 JSON → 透传 written/count/batch。"""
    import subprocess as sp
    import api.routers.iterate as it

    class _Proc:
        returncode = 0
        stdout = ('日志若干行\n{"written": ["sug_20260722_001"],'
                  ' "count": 1, "batch": "batch_20260722_120000"}')
        stderr = ""

    monkeypatch.setattr(it.subprocess, "run",
                        lambda *a, **k: _Proc())
    r = client.post("/api/iterate/suggest",
                    json={"question": "上次失败了怎么调", "favorite_id": "fav_a"})
    assert r.status_code == 200
    d = r.json()
    assert d["written"] == ["sug_20260722_001"]
    assert d["count"] == 1
    assert d["batch"] == "batch_20260722_120000"
    assert sp is it.subprocess  # 确实走 subprocess 通道


def test_iterate_suggest_interpreter_missing_503(monkeypatch):
    """解释器不存在 → 明确 503，且不真正起 subprocess。"""
    import api.routers.iterate as it
    monkeypatch.setattr(it, "ITERATE_PYTHON", r"E:\不存在的路径\python.exe")

    def _boom(*a, **k):
        raise AssertionError("解释器缺失时不应调用 subprocess.run")

    monkeypatch.setattr(it.subprocess, "run", _boom)
    r = client.post("/api/iterate/suggest", json={"question": "怎么调"})
    assert r.status_code == 503
    assert "迭代建议生成暂不可用" in r.json()["detail"]


def test_iterate_adopt_success(isolated_iterate):
    sugs, plans, favs = isolated_iterate
    _write_suggestion(sugs, suggestion_id="sug_20260722_001",
                      favorite_id="fav_a")
    favs.mkdir(parents=True, exist_ok=True)
    import json as _json
    (favs / "fav_a.json").write_text(_json.dumps({
        "id": "fav_a",
        "aldehyde": {"smiles": "O=Cc1ccccc1", "name": "苯甲醛"},
        "amine": {"smiles": "Nc1ccccc1", "name": "苯胺"},
    }, ensure_ascii=False), encoding="utf-8")
    r = client.post("/api/iterate/adopt",
                    json={"suggestion_id": "sug_20260722_001"})
    assert r.status_code == 200
    d = r.json()
    assert d["plan_id"].startswith("plan_")
    assert d["seq"] == 1
    assert d["template_name"]
    assert d["favorite_id"] == "fav_a"
    # 落盘 + 回写建议状态
    assert (plans / f"{d['plan_id']}.json").exists()
    r2 = client.get("/api/iterate/suggestions", params={"status": "adopted"})
    assert r2.json()["count"] == 1


def test_iterate_adopt_error_400(isolated_iterate):
    """AdoptError（建议不存在）→ 400 中文文案。"""
    r = client.post("/api/iterate/adopt",
                    json={"suggestion_id": "sug_20990101_999"})
    assert r.status_code == 400
    assert "不存在" in r.json()["detail"]


def test_iterate_plans_list(isolated_iterate, tmp_path, monkeypatch):
    import json as _json
    import api.routers.iterate as it
    plans_dir = tmp_path / "plans2"
    plans_dir.mkdir()
    monkeypatch.setattr(it, "PLANS_DIR", plans_dir)
    for pid, fid, ts in (("plan_20260722_001", "fav_a",
                          "2026-07-22T10:00:00+08:00"),
                         ("plan_20260722_002", None,
                          "2026-07-22T15:00:00+08:00")):
        (plans_dir / f"{pid}.json").write_text(_json.dumps({
            "plan_id": pid, "favorite_id": fid, "created_at": ts}),
            encoding="utf-8")
    (plans_dir / "plan_20260722_003.json").write_text("{坏", encoding="utf-8")
    r = client.get("/api/iterate/plans")
    assert r.status_code == 200
    ids = [p["plan_id"] for p in r.json()["plans"]]
    assert ids == ["plan_20260722_002", "plan_20260722_001"]  # 倒序+坏文件跳过
    r = client.get("/api/iterate/plans", params={"favorite_id": "fav_a"})
    assert [p["plan_id"] for p in r.json()["plans"]] == ["plan_20260722_001"]


def test_monomer_props_batch_partial_invalid():
    """批量性质卡：单项非法 SMILES 返回 error，不影响合法项。"""
    r = client.post("/api/monomers/props/batch", json={"items": [
        {"smiles": "Nc1ccccc1", "name": "苯胺"},
        {"smiles": "not_a_smiles!!", "name": "坏的"},
    ]})
    assert r.status_code == 200
    results = r.json()["results"]
    assert len(results) == 2
    ok, bad = results
    assert "error" not in ok
    assert ok["facts"]["mw"] > 90
    assert "非法 SMILES" in bad["error"]
