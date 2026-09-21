"""C5 图谱导出测试：JSON Canvas 1.0 / GraphML / 离线 HTML / 防爆炸上限 / 种子邻域。"""

from __future__ import annotations

import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for p in (str(PROJECT_ROOT), str(PROJECT_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from literature import graph_export, graph_extend, library_db  # noqa: E402

PAPERS = {str(i): {"title": f"Paper {i}", "year": 2020 + i % 5, "doi": f"10.1/{i}",
                   "journal": "Talanta", "source": "builtin"} for i in range(1, 8)}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    data, res = tmp_path / "data", tmp_path / "res"
    (data / "literature").mkdir(parents=True)
    (res / "data").mkdir(parents=True)
    (res / "data" / "paper_titles.json").write_text(
        json.dumps(PAPERS, ensure_ascii=False), encoding="utf-8")
    patched = []
    for name in ("src.runtime_config", "runtime_config"):
        mod = sys.modules.get(name)
        if mod is not None and hasattr(mod, "user_data_root"):
            monkeypatch.setattr(mod, "user_data_root", lambda: data)
            monkeypatch.setattr(mod, "resource_root", lambda: res)
            patched.append(name)
    assert patched
    monkeypatch.setattr(library_db, "db_path", lambda: data / "literature" / "library.db")
    monkeypatch.setenv("COF_DATA_DIR", str(data))
    library_db.reset_ready_cache()
    yield {"data": data}
    library_db.reset_ready_cache()


@pytest.fixture()
def conn(env):
    library_db.ensure_ready()
    c = library_db.connect(env["data"] / "literature" / "library.db")
    # 1→2→3→4 链式引用；5-6 相似；7 孤立
    for s, o in (("1", "2"), ("2", "3"), ("3", "4")):
        graph_extend._upsert_edge(c, f"paper:{s}", "cites", f"paper:{o}",
                                  source="crossref", confidence=1.0)
    graph_extend._upsert_edge(c, "paper:5", "similar_to", "paper:6",
                              source="embedding", confidence=0.88)
    c.commit()
    yield c
    c.close()


def test_json_canvas_shape_and_labels(conn):
    out = graph_export.to_json_canvas(conn)
    canvas = out["canvas"]
    assert out["counts"]["nodes"] == 6 and out["counts"]["edges"] == 4
    ids = {n["id"] for n in canvas["nodes"]}
    assert "paper:1" in ids and "paper:7" not in ids      # 孤立节点不在图上
    node = next(n for n in canvas["nodes"] if n["id"] == "paper:1")
    assert {"id", "type", "x", "y", "width", "height", "text"} <= set(node.keys())
    assert node["type"] == "text" and "Paper 1" in node["text"]
    rel = {e["label"] for e in canvas["edges"]}
    assert rel == {"引用", "相似"}
    assert all({"id", "fromNode", "toNode"} <= set(e.keys()) for e in canvas["edges"])
    # 可被 JSON 序列化（Obsidian 直接吃）
    json.dumps(canvas, ensure_ascii=False)


def test_seed_neighborhood_depth_one(conn):
    """防爆炸：从 1 出发一层深 → 只应包含 1 与其直接邻居（不含 3、4）。"""
    out = graph_export.to_json_canvas(conn, paper_id="1", depth=1)
    ids = {n["id"] for n in out["canvas"]["nodes"]}
    assert ids == {"paper:1", "paper:2"}
    assert out["seed"] == "paper:1"


def test_max_nodes_truncation_flag(conn):
    out = graph_export.to_json_canvas(conn, max_nodes=3)
    assert out["counts"]["nodes"] == 3 and out["truncated"] is True
    small = graph_export.to_json_canvas(conn, max_nodes=50)
    assert small["truncated"] is False


def test_graphml_is_valid_xml_with_nodes_and_edges(conn):
    xml = graph_export.to_graphml(conn)
    root = ET.fromstring(xml)
    ns = {"g": "http://graphml.graphdrawing.org/xmlns"}
    nodes = root.findall(".//g:node", ns)
    edges = root.findall(".//g:edge", ns)
    assert len(nodes) == 6 and len(edges) == 4
    labels = [d.text for d in root.findall(".//g:node/g:data[@key='label']", ns)]
    assert any("Paper 1" == (l or "") for l in labels)


def test_html_is_offline_selfcontained(conn):
    page = graph_export.to_html(conn)
    assert page.startswith("<!DOCTYPE html>") and "</html>" in page
    # 不引用任何外部资源（离线可用）
    assert not re.search(r"https?://(?!graphml)", page.replace(
        "http://graphml.graphdrawing.org", "")), "不应包含外部 URL"
    assert "src=" not in page and "cdn" not in page.lower()
    # 数据内嵌且计数写入标题栏
    assert '"paper:1"' in page and "节点 6" in page and "边 4" in page
    # 引用/相似两种边都有图例
    assert "引用" in page and "相似" in page
