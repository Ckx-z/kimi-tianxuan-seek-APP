"""科研知识库（v1.9.0）测试：条目校验/检索/数值过滤 + 解析降级 + 设置 + API。

路径全部隔离到 tmp_path；文献解析 LLM 默认未启用（走正则降级），不依赖网络。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for p in (str(PROJECT_ROOT), str(PROJECT_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

# 与路由同一 import 口径（module 身份一致，monkeypatch 才生效）
from literature import knowledge, llm_extract  # noqa: E402
from literature import embedding, graph_ingest  # noqa: E402
from references import titles  # noqa: E402

TFPT = "O=Cc1ccc(-c2nc(-c3ccc(C=O)cc3)nc(-c3ccc(C=O)cc3)n2)cc1"
B5 = "Nc1ccc(C(F)(F)F)cc1-c1ccc(N)cc1C(F)(F)F"
TP = "O=CC1=C(C=O)C(=O)C(C=O)=C1O"
PA = "Nc1ccc(N)cc1"


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(knowledge, "ENTRIES_PATH",
                        tmp_path / "literature" / "knowledge_entries.jsonl")
    monkeypatch.setattr(llm_extract, "SETTINGS_PATH",
                        tmp_path / "config" / "literature_llm_settings.local.json")
    monkeypatch.setattr(graph_ingest, "_app_root", lambda: tmp_path)
    monkeypatch.setattr(embedding, "EMB_PATH",
                        tmp_path / "graphrag_user" / "literature_emb.jsonl")
    monkeypatch.setattr(titles, "TITLES_PATH", tmp_path / "paper_titles.json")
    (tmp_path / "paper_titles.json").write_text(
        json.dumps({"1": {"title": "TFPT 文献", "doi": "10.1000/xyz"}}),
        encoding="utf-8")
    yield tmp_path


@pytest.fixture()
def client():
    from api.main import app
    return TestClient(app)


def _pair(group="G1", label=1.0, kind="film_outcome"):
    return {
        "kind": kind, "group_id": group,
        "experiment": f"{group}：TFPT+B5 管壁扩散成膜",
        "ald_smiles": TFPT, "amine_smiles": B5,
        "film_label": label, "synthesis_method": "管壁扩散-调制剂",
        "evidence": "壁上形成连续光滑的薄膜（原文）",
    }


def _char(group="G1", technique="PL"):
    is_bet = technique == "BET"
    return {
        "kind": "characterization", "group_id": group,
        "experiment": f"{group} 表征",
        "technique": technique, "sample": "TFPT-B5 膜",
        "metrics": [{"name": "比表面积" if is_bet else "PLQY",
                     "value": 1234 if is_bet else 41.3,
                     "unit": "m²/g" if is_bet else "%"}],
        "conclusion": "强荧光" if not is_bet else "高比表面积",
        "evidence": "PLQY 为 41.3%（原文）" if not is_bet
                    else "比表面积 1234 m²/g（原文）",
    }


# ---------------------------------------------------------------- 校验

def test_validate_entry_requirements():
    with pytest.raises(ValueError, match="group_id"):
        knowledge.validate_entry({**_pair(), "group_id": ""})
    with pytest.raises(ValueError, match="evidence"):
        knowledge.validate_entry({**_pair(), "evidence": ""})
    with pytest.raises(ValueError, match="kind"):
        knowledge.validate_entry({**_pair(), "kind": "whatever"})
    with pytest.raises(ValueError, match="film_label"):
        knowledge.validate_entry({**_pair(), "film_label": 0.7})
    with pytest.raises(ValueError, match="ald_smiles"):
        knowledge.validate_entry({**_pair(), "ald_smiles": ""})


def test_validate_characterization_metrics():
    rec = knowledge.validate_entry(_char())
    assert rec["technique"] == "PL"
    assert rec["metrics"][0] == {"name": "PLQY", "value": 41.3, "unit": "%"}
    # 无有效数值 → 拒绝
    bad = _char()
    bad["metrics"] = [{"name": "PLQY", "value": "非数字"}]
    with pytest.raises(ValueError, match="metrics"):
        knowledge.validate_entry(bad)
    # 非法 technique → 拒绝
    bad2 = _char()
    bad2["technique"] = "RAMAN"
    with pytest.raises(ValueError, match="technique"):
        knowledge.validate_entry(bad2)


def test_add_entries_atomic():
    # 一批中有一条非法 → 整批不入库
    with pytest.raises(ValueError):
        knowledge.add_entries("1", [_pair(), {**_pair(), "group_id": ""}])
    assert knowledge.list_entries() == []


# ---------------------------------------------------------------- 检索

def test_search_and_metric_filter(tmp_path):
    knowledge.add_entries("1", [
        _pair("G1", 1.0),
        _pair("G2", 0.0),                     # 负样本
        _char("G1", "PL"),
        _char("G2", "BET"),
    ])
    assert len(knowledge.list_entries(paper_id="1")) == 4
    assert len(knowledge.list_entries(kind="film_outcome")) == 2
    assert len(knowledge.list_entries(film_label=0.0)) == 1  # 负样本可查
    assert len(knowledge.list_entries(technique="PL")) == 1
    # 数值范围：PLQY >= 40
    hits = knowledge.list_entries(metric="PLQY", min_value=40)
    assert len(hits) == 1 and hits[0]["technique"] == "PL"
    assert knowledge.list_entries(metric="PLQY", min_value=50) == []


def test_update_and_delete():
    entries = knowledge.add_entries("1", [_pair()])
    eid = entries[0]["entry_id"]
    updated = knowledge.update_entry(eid, {**_pair(), "group_id": "G3"})
    assert updated["group_id"] == "G3"
    assert knowledge.update_entry("ke_000000000000", _pair()) is None
    removed = knowledge.delete_entry(eid)
    assert removed["entry_id"] == eid
    assert knowledge.get_entry(eid) is None


def test_group_by():
    entries = knowledge.add_entries("1", [_pair("G1"), _char("G1"), _pair("G2")])
    groups = knowledge.group_by(entries)
    assert set(groups) == {"G1", "G2"}
    assert len(groups["G1"]) == 2


# ---------------------------------------------------------------- 解析降级与设置

def test_parse_disabled_falls_back_to_smiles_scan():
    from rdkit import Chem
    res = llm_extract.parse_text(f"体系含 {TFPT} 与 {B5} 缩聚。")
    assert res["llm_used"] is False
    assert any(e["kind"] == "monomer_pair" for e in res["entries"])
    pair = next(e for e in res["entries"] if e["kind"] == "monomer_pair")
    # 扫描输出 canonical SMILES，按 canonical 比较
    assert Chem.MolToSmiles(Chem.MolFromSmiles(pair["ald_smiles"])) == \
        Chem.MolToSmiles(Chem.MolFromSmiles(TFPT))
    assert Chem.MolToSmiles(Chem.MolFromSmiles(pair["amine_smiles"])) == \
        Chem.MolToSmiles(Chem.MolFromSmiles(B5))


def test_settings_save_get_masked(tmp_path):
    s = llm_extract.save_settings(
        enabled=True, base_url="https://api.example.com/v1",
        api_key="sk-test-abcdefghij", model="test-model",
        embedding_provider="local",
        embedding_model="Qwen/Qwen3-Embedding-0.6B")
    assert "…" in s["api_key"]
    assert s["api_key"] != "sk-test-abcdefghij"
    assert s["embedding_model"] == "Qwen/Qwen3-Embedding-0.6B"
    # 掩码值再保存不应覆盖真实 key
    llm_extract.save_settings(api_key=s["api_key"])
    assert llm_extract._read_settings()["api_key"] == "sk-test-abcdefghij"
    # 未配置完整时 is_enabled False（缺真实可用端点也按不可用处理由
    # test_connection 验证；这里验证掩码不污染）
    assert llm_extract.test_connection()["ok"] is False


# ---------------------------------------------------------------- API

def test_entries_api_roundtrip(client):
    # 解析（LLM 未启用 → 正则降级）
    r = client.post("/api/literature/1/parse",
                    data={"text": f"体系含 {TFPT} 与 {B5}。"})
    assert r.status_code == 200
    preview = r.json()
    assert preview["llm_used"] is False and preview["entries"]

    # 入库（含负样本）
    add = client.post("/api/literature/1/entries", json={"entries": [
        _pair("G1", 1.0), _pair("G2", 0.0), _char("G1", "PL")]})
    assert add.status_code == 201
    eids = [e["entry_id"] for e in add.json()["entries"]]

    # 分组查询
    got = client.get("/api/literature/1/entries").json()
    assert got["count"] == 3
    assert set(got["groups"]) == {"G1", "G2"}

    # 跨文献检索（数值范围）
    hits = client.get("/api/literature/entries",
                      params={"metric": "PLQY", "min": 40}).json()
    assert hits["count"] == 1

    # 编辑 / 删除
    up = client.patch(f"/api/literature/entries/{eids[0]}",
                      json={"entry": {**_pair("G9", 1.0)}})
    assert up.status_code == 200 and up.json()["group_id"] == "G9"
    d = client.delete(f"/api/literature/entries/{eids[1]}")
    assert d.status_code == 200
    assert client.get("/api/literature/1/entries").json()["count"] == 2


def test_entries_api_validation_and_404(client):
    assert client.post("/api/literature/999/parse",
                       data={"text": "x"}).status_code == 404
    bad = client.post("/api/literature/1/entries",
                      json={"entries": [{**_pair(), "group_id": ""}]})
    assert bad.status_code == 400
    assert client.patch("/api/literature/entries/ke_000000000000",
                        json={"entry": _pair()}).status_code == 404
    assert client.delete("/api/literature/entries/ke_000000000000"
                         ).status_code == 404


def test_entry_to_gnn_feedback_and_dft(client, monkeypatch, tmp_path):
    from src.predictor import gnn_feedback
    monkeypatch.setattr(gnn_feedback, "FEEDBACK_PATH",
                        tmp_path / "gnn_fb.jsonl")
    monkeypatch.setattr(gnn_feedback, "_base_keys", None)
    add = client.post("/api/literature/1/entries",
                      json={"entries": [_pair("G1", 0.0)]}).json()
    eid = add["entries"][0]["entry_id"]
    # film_outcome 负样本 → GNN 反馈
    fb = client.post(f"/api/literature/entries/{eid}/to-gnn-feedback")
    assert fb.status_code == 201
    assert fb.json()["label"] == 0.0
    # 非 film_outcome → 400
    add2 = client.post("/api/literature/1/entries",
                       json={"entries": [_char("G1", "PL")]}).json()
    assert client.post(
        f"/api/literature/entries/{add2['entries'][0]['entry_id']}"
        f"/to-gnn-feedback").status_code == 400
    # DFT 预填参数
    dft = client.post(f"/api/literature/entries/{eid}/to-dft")
    assert dft.status_code == 200
    assert dft.json()["ald_smiles"] == TFPT
    assert "/toolbox/dft?a=" in dft.json()["url"]


def test_llm_settings_api(client):
    put = client.put("/api/literature/llm-settings", json={
        "enabled": True, "base_url": "https://api.example.com/v1",
        "api_key": "sk-secret-123456", "model": "m",
        "embedding_provider": "off"})
    assert put.status_code == 200
    assert put.json()["enabled"] is True
    assert "…" in put.json()["api_key"]
    assert client.get("/api/literature/llm-settings").json()["embedding_provider"] == "off"
    test = client.post("/api/literature/llm-settings/test")
    assert test.json()["ok"] is False  # 假端点不可达 → 连接失败


def test_paper_detail_api(client):
    """老文献自带的结构化元数据（作者/期刊/摘要）可完整取回（v1.9.1）。"""
    r = client.get("/api/literature/papers/1")
    assert r.status_code == 200
    d = r.json()
    assert d["title"] == "TFPT 文献"
    assert d["doi"] == "10.1000/xyz"
    assert d["url"] == "https://doi.org/10.1000/xyz"
    assert client.get("/api/literature/papers/999").status_code == 404


# ---------------------------------------------------------------- 入图（P1）

def test_graph_sync_creates_lit_nodes_and_edges():
    entries = knowledge.add_entries("1", [
        {**_pair("G1", 1.0)},
        {**_pair("G2", 0.0), "experiment": "G2：不成膜（负样本）"},
        {**_char("G1", "PL")},
        {"kind": "condition", "group_id": "G1", "experiment": "G1 条件",
         "conditions": {"solvent": "甲苯/氯仿", "catalyst": "乙酸",
                        "modulator": "苯胺"},
         "evidence": "甲苯/氯仿体系（原文）"},
    ])
    n = graph_ingest.sync_groups(entries)
    assert n == 2  # G1/G2 两组

    import pickle
    fp = graph_ingest._app_root() / "data" / "graphrag_user" / "graph_user.pkl"
    G = pickle.load(open(fp, "rb"))
    nid = graph_ingest.lit_node_id("1", "G1")
    assert nid in G
    attrs = G.nodes[nid]
    assert attrs["source"] == "literature"
    assert attrs["node_type"] == "reaction"
    assert attrs["film_label"] == 1.0
    assert "PLQY" in attrs["characterizations"]
    # 单体节点 + 边（醛/胺/溶剂/催化剂/产物）
    mid = "M-" + __import__("hashlib").md5(TFPT.encode()).hexdigest()[:12]
    assert mid in G
    assert G.has_edge(nid, mid)
    edges = {d.get("edge_type") for _, _, d in G.edges(nid, data=True)}
    assert "reaction_uses_aldehyde" in edges
    assert "reaction_uses_amine" in edges
    assert "reaction_uses_solvent" in edges
    assert "reaction_uses_catalyst" in edges
    assert "reaction_produces" in edges
    # 负样本组 → O-failed 边
    nid2 = graph_ingest.lit_node_id("1", "G2")
    assert nid2 in G
    oedges = {v for _, v, d in G.edges(nid2, data=True)
              if d.get("edge_type") == "reaction_produces"}
    assert "O-failed" in oedges


def test_graph_sync_group_removed_when_empty():
    entries = knowledge.add_entries("1", [_pair("G1", 1.0)])
    graph_ingest.sync_groups(entries)
    import pickle
    fp = graph_ingest._app_root() / "data" / "graphrag_user" / "graph_user.pkl"
    # 组内条目全删 → 节点移除
    for e in entries:
        knowledge.delete_entry(e["entry_id"])
    graph_ingest.sync_group("1", "G1", entries=[])
    G = pickle.load(open(fp, "rb"))
    assert graph_ingest.lit_node_id("1", "G1") not in G


def test_api_entries_sync_graph(client):
    add = client.post("/api/literature/1/entries",
                      json={"entries": [_pair("G1", 1.0)]})
    assert add.status_code == 201
    eid = add.json()["entries"][0]["entry_id"]
    import pickle
    fp = graph_ingest._app_root() / "data" / "graphrag_user" / "graph_user.pkl"
    G = pickle.load(open(fp, "rb"))
    assert graph_ingest.lit_node_id("1", "G1") in G
    # 删除条目 → 组节点移除
    client.delete(f"/api/literature/entries/{eid}")
    G2 = pickle.load(open(fp, "rb"))
    assert graph_ingest.lit_node_id("1", "G1") not in G2


# ---------------------------------------------------------------- embedding（P3）

def test_embedding_off_by_default():
    assert embedding.provider() == "off"  # 设置未配置 → off
    assert embedding.embed_texts(["hello"]) is None
    assert embedding.search("hello") == []
    entries = knowledge.add_entries("1", [_pair()])
    assert embedding.sync_entries(entries) == 0  # off → 跳过
    assert embedding.status()["provider"] == "off"


def test_embedding_sync_and_search_stubbed(monkeypatch):
    monkeypatch.setattr(embedding, "provider", lambda: "local")
    fake_vecs = {"a": [1.0, 0.0], "b": [0.0, 1.0]}
    monkeypatch.setattr(
        embedding, "_embed_local",
        lambda texts: [fake_vecs["a"], fake_vecs["b"], [0.0, 1.0]][:len(texts)])
    entries = knowledge.add_entries("1", [_pair("G1"), _char("G1", "PL")])
    assert embedding.sync_entries(entries) == 2
    # 向量已落盘
    rows = embedding._load()
    assert len(rows) == 2
    # 检索：查询向量与「a」条目内积最大
    monkeypatch.setattr(embedding, "_embed_local",
                        lambda texts: [[1.0, 0.0]] * len(texts))
    hits = embedding.search("query")
    assert hits and hits[0]["entry_id"] == entries[0]["entry_id"]
    # 删除同步
    assert embedding.remove_entry(entries[0]["entry_id"]) is True
    assert embedding.remove_entry(entries[0]["entry_id"]) is False


def test_embedding_status_api(client):
    r = client.get("/api/literature/embedding-status")
    assert r.status_code == 200
    assert r.json()["provider"] == "off"
