"""视觉读图（v1.9.4 方案 B，可选开关）测试。

要点：
- 默认关闭 → 方案 A（抽图入库）不受影响；
- 开关 + 模型配置齐备才 available；端点/key 留空回退主解析 LLM；
- 视觉调用全部打桩（不打真实网络），验证 JSON 解析、metrics 提取、
  条目构造（technique 规范 → characterization；否则退化 conclusion）；
- API 层：未启用 → 400 明确提示；启用 → 返回可勾选条目并带 source=vision_extract。
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

from literature import (figures, graph_ingest, knowledge, llm_extract,  # noqa: E402
                        pdf_figures, vision)
from references import titles  # noqa: E402

from api.main import app  # noqa: E402

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    # llm_extract 可能以裸名与 src.* 两种形式各被 import 一次（双实例）：
    # 两边的 SETTINGS_PATH 都要打桩，否则 vision 读到真实配置
    for mod_name in ("literature.llm_extract", "src.literature.llm_extract"):
        mod = sys.modules.get(mod_name)
        if mod is not None and hasattr(mod, "SETTINGS_PATH"):
            monkeypatch.setattr(mod, "SETTINGS_PATH",
                                tmp_path / "config" / "lit_llm.json")
    monkeypatch.setattr(llm_extract, "SETTINGS_PATH",
                        tmp_path / "config" / "lit_llm.json")
    monkeypatch.setattr(figures, "FIGURES_DIR", tmp_path / "literature" / "figures")
    monkeypatch.setattr(figures, "INDEX_PATH",
                        tmp_path / "literature" / "figures_index.json")
    monkeypatch.setattr(figures, "_cache", None)
    monkeypatch.setattr(pdf_figures, "STAGING_DIR",
                        tmp_path / "literature" / "figure_staging")
    monkeypatch.setattr(knowledge, "ENTRIES_PATH",
                        tmp_path / "literature" / "knowledge_entries.jsonl")
    monkeypatch.setattr(graph_ingest, "_app_root", lambda: tmp_path)
    patched = []
    for mod_name in ("references.titles", "src.references.titles"):
        mod = sys.modules.get(mod_name)
        if mod is not None and mod not in patched:
            monkeypatch.setattr(mod, "TITLES_PATH", tmp_path / "paper_titles.json")
            patched.append(mod)
    (tmp_path / "paper_titles.json").write_text(json.dumps({
        "1": {"title": "视觉读图测试文献"}}, ensure_ascii=False), encoding="utf-8")
    for mod in patched:
        mod.reload()
    yield tmp_path
    for mod in patched:
        mod.reload()


def _enable_vision(**kw):
    llm_extract.save_settings(
        enabled=True, base_url="https://api.example.com/v1",
        api_key="sk-main-key-1234567890", model="text-model",
        **kw)


# ---------------------------------------------------------------- 设置 / 状态

def test_vision_disabled_by_default(isolate):
    assert vision.is_enabled() is False
    st = vision.status()
    assert st["enabled"] is False and st["available"] is False


def test_vision_settings_and_fallback(isolate):
    _enable_vision(vision_enabled=True, vision_model="qwen-vl-max")
    cfg = vision.resolve_config()
    assert cfg["enabled"] and cfg["model"] == "qwen-vl-max"
    # 端点/key 留空 → 回退主解析 LLM
    assert cfg["base_url"] == "https://api.example.com/v1"
    assert cfg["api_key"] == "sk-main-key-1234567890"
    assert cfg["inherits_main"] is True
    assert vision.is_enabled() is True
    assert vision.status()["available"] is True

    # 独立端点/key
    _enable_vision(vision_enabled=True, vision_model="gpt-4o",
                   vision_base_url="https://vision.example.com/v1",
                   vision_api_key="sk-vision-key-0987654321")
    cfg2 = vision.resolve_config()
    assert cfg2["base_url"] == "https://vision.example.com/v1"
    assert cfg2["api_key"] == "sk-vision-key-0987654321"
    assert cfg2["inherits_main"] is False


def test_vision_key_masked_in_public_settings(isolate):
    _enable_vision(vision_enabled=True, vision_model="qwen-vl-max",
                   vision_api_key="sk-vision-key-0987654321")
    public = llm_extract.get_settings()
    assert "…" in public["vision_api_key"]
    assert public["vision_api_key"] != "sk-vision-key-0987654321"
    assert public["vision_enabled"] is True
    assert "vision_api_key" in llm_extract.get_settings(public=False)


def test_vision_enabled_needs_model(isolate):
    _enable_vision(vision_enabled=True)          # 只开开关，没填模型
    assert vision.is_enabled() is False


# ---------------------------------------------------------------- 调用（打桩）

def test_analyze_image_parses_metrics(isolate, monkeypatch):
    _enable_vision(vision_enabled=True, vision_model="qwen-vl-max")
    payload = json.dumps({
        "figure_type": "spectra",
        "technique": "PXRD",
        "description": "该图为不同溶剂条件下的 PXRD 图谱对比。",
        "metrics": [{"name": "2θ峰位", "value": 3.52, "unit": "°"},
                    {"name": "FWHM", "value": 0.39, "unit": "°"},
                    {"name": "坏数据", "value": "非数字"}],
        "confidence": "high",
        "notes": "峰位按主峰读取",
    }, ensure_ascii=False)
    monkeypatch.setattr(vision, "_chat_vision",
                        lambda *a, **kw: (payload, {"max_tokens": 16000}))
    res = vision.analyze_image(b"\x89PNG" + b"x" * 100, mime="image/png")
    assert res["ok"] is True
    assert res["technique"] == "PXRD"
    assert len(res["metrics"]) == 2          # 非数值被丢弃
    assert res["metrics"][0]["value"] == 3.52
    assert res["confidence"] == "high"


def test_analyze_image_handles_bad_output_and_errors(isolate, monkeypatch):
    _enable_vision(vision_enabled=True, vision_model="qwen-vl-max")
    monkeypatch.setattr(vision, "_chat_vision",
                        lambda *a, **kw: ("抱歉，我无法识别这张图。", {}))
    res = vision.analyze_image(b"\x89PNG" + b"x" * 50)
    assert res["ok"] is False and "无法解析" in res["error"]
    monkeypatch.setattr(vision, "_chat_vision",
                        lambda *a, **kw: (None, {"error": "URLError: timeout"}))
    res2 = vision.analyze_image(b"\x89PNG" + b"x" * 50)
    assert res2["ok"] is False and "timeout" in res2["error"]
    # 超大图直接拒绝，不调用模型
    big = b"\x89PNG" + b"x" * (vision.MAX_IMAGE_BYTES + 1)
    res3 = vision.analyze_image(big)
    assert res3["ok"] is False and "超过" in res3["error"]
    # 未启用
    llm_extract.save_settings(vision_enabled=False)
    assert vision.analyze_image(b"\x89PNG")["ok"] is False


# ---------------------------------------------------------------- 条目构造

def test_propose_entry_characterization_and_conclusion():
    meta = {"caption_label": "Fig. 3", "page": 5,
            "caption": "Figure 3. PXRD patterns of the COFs."}
    entry = vision.propose_entry(meta, {
        "ok": True, "technique": "xrd", "description": "不同溶剂的 PXRD 对比",
        "metrics": [{"name": "2θ", "value": 3.5, "unit": "°"}],
        "confidence": "high", "notes": "主峰位置",
    })
    assert entry["kind"] == "characterization"
    assert entry["technique"] == "PXRD"                 # 别名归一化
    assert entry["source"] == "vision_extract"
    assert entry["source_file"] == "[图 Fig. 3]"
    assert "视觉读图" in entry["evidence"]
    # 技术不可识别 → 退化 conclusion（不丢描述）
    entry2 = vision.propose_entry(meta, {
        "ok": True, "technique": "RAMAN", "description": "拉曼光谱示意",
        "metrics": [{"name": "shift", "value": 1000}],
        "confidence": "low", "notes": "",
    })
    assert entry2["kind"] == "conclusion"
    assert entry2["conclusion"].startswith("拉曼")
    # 失败 → 不产出条目
    assert vision.propose_entry(meta, {"ok": False}) is None


# ---------------------------------------------------------------- API

def _stage_one_figure(tmp_path) -> str:
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=300, height=220)
    page.insert_text((20, 40), "PXRD", fontsize=20)
    blob = page.get_pixmap().tobytes("png")
    doc.close()
    cands = [{"kind": "embedded", "ext": ".png", "data": blob,
              "caption": "Figure 3. PXRD patterns.", "caption_label": "Fig. 3",
              "figure_type": "spectra", "page": 5, "width": 300, "height": 220,
              "sha1": "x" * 40}]
    staged = pdf_figures.stage_candidates("1", cands)
    return staged[0]["staged_id"]


def test_analyze_api_requires_vision(isolate):
    sid = _stage_one_figure(isolate)
    r = client.post("/api/literature/1/figures/analyze",
                    json={"staged_ids": [sid]})
    assert r.status_code == 400
    assert "视觉读图未启用" in r.json()["detail"]


def test_analyze_api_returns_entries(isolate, monkeypatch):
    _enable_vision(vision_enabled=True, vision_model="qwen-vl-max")
    sid = _stage_one_figure(isolate)
    monkeypatch.setattr(vision, "analyze_image", lambda data, **kw: {
        "ok": True, "error": None, "figure_type": "spectra",
        "technique": "BET", "description": "N2 吸附等温线",
        "metrics": [{"name": "比表面积", "value": 2105, "unit": "m2/g"}],
        "confidence": "high", "notes": "由等温线读取",
    })
    r = client.post("/api/literature/1/figures/analyze",
                    json={"staged_ids": [sid], "max_figures": 3})
    assert r.status_code == 200
    body = r.json()
    assert body["ok_count"] == 1 and body["metric_total"] == 1
    assert len(body["entries"]) == 1
    e = body["entries"][0]
    assert e["kind"] == "characterization" and e["technique"] == "BET"
    assert e["source"] == "vision_extract"
    assert e["valid"] is True                     # 试校验标注随响应返回
    assert body["vision"]["available"] is True


def test_analyze_api_missing_staged(isolate, monkeypatch):
    _enable_vision(vision_enabled=True, vision_model="qwen-vl-max")
    r = client.post("/api/literature/1/figures/analyze",
                    json={"staged_ids": ["fs_000000000000"]})
    assert r.status_code == 200
    assert r.json()["ok_count"] == 0
    assert "暂存" in r.json()["results"][0]["error"]


def test_llm_settings_api_exposes_vision(isolate):
    # 视觉模型自带端点/key（不依赖主解析 LLM）
    r = client.put("/api/literature/llm-settings", json={
        "vision_enabled": True, "vision_model": "qwen-vl-max",
        "vision_base_url": "https://vision.example.com/v1",
        "vision_api_key": "sk-vision-key-0987654321",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["vision_enabled"] is True
    assert "…" in body["vision_api_key"]
    got = client.get("/api/literature/llm-settings").json()
    assert got["vision_status"]["available"] is True
    assert got["vision_status"]["model"] == "qwen-vl-max"
    assert got["vision_status"]["inherits_main"] is False
    # 主解析 LLM 配好、视觉端点/key 留空 → 沿用主配置（inherits_main=True）
    _enable_vision(vision_enabled=True, vision_model="qwen-vl-max",
                   vision_base_url="", vision_api_key="")
    got2 = client.get("/api/literature/llm-settings").json()
    assert got2["vision_status"]["available"] is True
    assert got2["vision_status"]["inherits_main"] is True
