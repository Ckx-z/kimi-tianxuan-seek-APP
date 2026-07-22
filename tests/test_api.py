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
