"""文献录入图谱（v1.7.0，需求三）测试：上传/标注/筛选/SMILES 渲染/删除/回写。

图谱目录与索引、文献库路径全部隔离到 tmp_path，不碰真实数据。
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
from literature import figures  # noqa: E402
from references import titles  # noqa: E402

PA = "Nc1ccc(N)cc1"  # 对苯二胺
TPT = "O=Cc1ccc(-c2nc(-c3ccc(C=O)cc3)nc(-c3ccc(C=O)cc3)n2)cc1"  # TFPT


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(figures, "FIGURES_DIR", tmp_path / "literature" / "figures")
    monkeypatch.setattr(figures, "INDEX_PATH", tmp_path / "literature" / "figures_index.json")
    figures._cache = None
    monkeypatch.setattr(titles, "TITLES_PATH", tmp_path / "paper_titles.json")
    (tmp_path / "paper_titles.json").write_text(
        json.dumps({"1": {"title": "TFPT 文献", "doi": "10.1000/xyz",
                          "journal": "J Test", "year": 2025}}),
        encoding="utf-8")
    yield tmp_path


@pytest.fixture()
def client():
    from api.main import app
    return TestClient(app)


def _upload(client, paper_id="1", figure_type="spectra", caption="图 2a：荧光光谱",
             tags="荧光,TFPT", meta=None, data=b"\x89PNG fake-bytes"):
    return client.post(
        f"/api/literature/{paper_id}/figures",
        files={"file": ("fig.png", data, "image/png")},
        data={
            "figure_type": figure_type,
            "caption": caption,
            "tags": tags,
            **({"meta_json": json.dumps(meta)} if meta is not None else {}),
        },
    )


# ---------------------------------------------------------------- 上传/标注

def test_upload_and_roundtrip(client):
    r = _upload(client)
    assert r.status_code == 201
    rec = r.json()
    assert rec["fig_id"].startswith("fig_")
    assert rec["paper_id"] == "1"
    assert rec["figure_type"] == "spectra"
    assert rec["tags"] == ["荧光", "TFPT"]
    assert rec["size"] == len(b"\x89PNG fake-bytes")

    lst = client.get("/api/literature/figures").json()["figures"]
    assert len(lst) == 1 and lst[0]["fig_id"] == rec["fig_id"]

    fr = client.get(f"/api/literature/figures/{rec['fig_id']}/file")
    assert fr.status_code == 200
    assert fr.content == b"\x89PNG fake-bytes"
    assert fr.headers["content-type"] == "image/png"

    # 标注更新 + 打分回写 score_note
    up = client.patch(
        f"/api/literature/figures/{rec['fig_id']}",
        json={"caption": "改后图注", "tags": ["光谱"],
              "meta": {"technique": "PL", "conditions": "120 °C, 48 h"},
              "score_note": "本系统打分 0.85，与文献一致"})
    assert up.status_code == 200
    assert up.json()["caption"] == "改后图注"
    assert up.json()["meta"]["technique"] == "PL"
    assert up.json()["score_note"] == "本系统打分 0.85，与文献一致"


def test_delete_removes_file_and_index(client):
    rec = _upload(client).json()
    dr = client.delete(f"/api/literature/figures/{rec['fig_id']}")
    assert dr.status_code == 200
    assert client.get(f"/api/literature/figures/{rec['fig_id']}").status_code == 404
    assert client.get("/api/literature/figures").json()["figures"] == []
    assert client.delete(f"/api/literature/figures/{rec['fig_id']}").status_code == 404


# ---------------------------------------------------------------- SMILES 渲染

def test_from_smiles_structure(client):
    r = client.post("/api/literature/figures/from-smiles",
                    json={"paper_id": "1", "smiles": TPT, "caption": "TFPT 结构式"})
    assert r.status_code == 201
    rec = r.json()
    assert rec["figure_type"] == "structure"
    assert rec["meta"]["smiles"] == TPT
    assert rec["mime"] in ("image/svg+xml", "image/png")
    fr = client.get(f"/api/literature/figures/{rec['fig_id']}/file")
    assert fr.status_code == 200 and len(fr.content) > 100


def test_from_smiles_bad_smiles(client):
    r = client.post("/api/literature/figures/from-smiles",
                    json={"paper_id": "1", "smiles": "not-a-smiles"})
    assert r.status_code == 400
    assert "无法解析" in r.json()["detail"]


# ---------------------------------------------------------------- 校验与筛选

def test_upload_validation_errors(client):
    assert _upload(client, figure_type="unknown").status_code == 400
    assert _upload(client, paper_id="999").status_code == 400  # 文献不存在
    # structure 类型必须带可解析 smiles 元数据
    r = _upload(client, figure_type="structure", meta={"smiles": "bad"})
    assert r.status_code == 400
    assert _upload(client, figure_type="structure",
                   meta={"smiles": PA}).status_code == 201


def test_list_filters(client):
    a = _upload(client, figure_type="spectra", caption="光谱").json()
    b = client.post("/api/literature/figures/from-smiles",
                    json={"paper_id": "1", "smiles": TPT}).json()
    assert len(client.get("/api/literature/figures").json()["figures"]) == 2
    only_struct = client.get("/api/literature/figures",
                             params={"figure_type": "structure"}).json()["figures"]
    assert [f["fig_id"] for f in only_struct] == [b["fig_id"]]
    by_tag = client.get("/api/literature/figures",
                        params={"tag": "荧光"}).json()["figures"]
    assert [f["fig_id"] for f in by_tag] == [a["fig_id"]]
    by_paper = client.get("/api/literature/figures",
                          params={"paper_id": "999"}).json()["figures"]
    assert by_paper == []


def test_papers_list(client):
    r = client.get("/api/literature/papers")
    assert r.status_code == 200
    papers = r.json()["papers"]
    assert any(p["paper_id"] == "1" and p["title"] == "TFPT 文献" for p in papers)
