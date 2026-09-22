"""P6 第一批（v2.0.1）：文献图片「视觉标注」+ 助手直接看图。

覆盖：
1. 数据层 `library_db.set_figure_caption`：写 caption/caption_source、meta_json
   **合并不丢已有键**、非法 source 抛 ValueError、fig_id 不存在返回 None；
   `get_figure` / `list_figures`（按 paper_id 过滤 + 按 page 排序）；
2. `vision.caption_figure`：未启用 → ok=False 不抛异常；启用（打桩 analyze_image）
   → 图注只由模型给的 description + metrics 拼成，不编造；
3. API：`POST /api/library/figures/{fig_id}/caption`（单图，409/404/失败 200+ok:false）
   与 `POST /api/library/papers/{paper_id}/figures/caption`（批量：跳过已有
   vision/user、force 重做、limit 生效、单张失败不影响其余）；
4. 助手工具 `view_figure`：已注册且 schema 齐全、视觉未启用时如实失败（中文原因）、
   list 能列图、save_caption 写回标注、confirm 只在写数据时要求二次确认。

隔离：runtime_config.user_data_root/resource_root、library_db.db_path、
figures.FIGURES_DIR/INDEX_PATH、titles.TITLES_PATH 全部 monkeypatch 到 tmp_path，
**不写仓库 data/ 与真实用户数据目录**。
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

from assistant import registry  # noqa: E402
from assistant.tools import litfigure  # noqa: E402
from literature import figure_caption, figures, library_db, vision  # noqa: E402

PAPERS = {
    "1": {"title": "视觉标注测试文献", "doi": "10.1000/v201", "source": "builtin"},
    "2": {"title": "另一篇", "doi": "10.1000/v202", "source": "builtin"},
}
PNG_HEAD = b"\x89PNG\r\n\x1a\n"

ANALYSIS = {
    "ok": True, "error": None, "figure_type": "spectra", "technique": "PXRD",
    "description": "该图为 COF 膜的 PXRD 图谱",
    "metrics": [{"name": "2θ", "value": 4.7, "unit": "°"},
                {"name": "2θ", "value": 27.1, "unit": "°"}],
    "confidence": "high", "notes": "峰位读数清晰", "budget_expanded": False,
}


# ---------------------------------------------------------------- 夹具

@pytest.fixture()
def env(tmp_path, monkeypatch):
    """数据根 + 图谱目录/索引 + 文献库 DB 全部隔离到 tmp_path。"""
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
    assert patched, "runtime_config 必须已被导入，否则隔离不生效"

    # 图谱（事实来源）：目录 + 索引 + 内存缓存
    monkeypatch.setattr(figures, "FIGURES_DIR", data / "literature" / "figures")
    monkeypatch.setattr(figures, "INDEX_PATH",
                        data / "literature" / "figures_index.json")
    monkeypatch.setattr(figures, "_cache", None)

    # 文献库（镜像）：DB 路径
    monkeypatch.setattr(library_db, "db_path",
                        lambda: data / "literature" / "library.db")
    monkeypatch.setenv("COF_DATA_DIR", str(data))

    # 文献库标题（figures.add_figure 会校验 paper_id 存在）
    titles_mod = None
    try:
        from references import titles as titles_mod  # noqa: PLC0415
    except ImportError:  # pragma: no cover
        from src.references import titles as titles_mod  # type: ignore
    monkeypatch.setattr(titles_mod, "TITLES_PATH",
                        data / "literature" / "paper_titles.json")
    (data / "literature" / "paper_titles.json").write_text(
        json.dumps(PAPERS, ensure_ascii=False), encoding="utf-8")
    titles_mod.reload()

    library_db.reset_ready_cache()
    yield {"data": data, "res": res, "tmp": tmp_path}
    titles_mod.reload()
    figures._cache = None
    library_db.reset_ready_cache()


@pytest.fixture()
def conn(env):
    library_db.ensure_ready()
    c = library_db.connect(env["data"] / "literature" / "library.db")
    yield c
    c.close()


@pytest.fixture()
def client(env):
    from api.main import app
    return TestClient(app)


def add_fig(paper_id: str = "1", page: int = 1, caption: str = "图 1：PXRD 图谱",
            marker: bytes = b"A", figure_type: str = "spectra") -> dict:
    """加一张图（写索引 + 落文件；file 为裸文件名，落在 monkeypatch 后的目录）。"""
    return figures.add_figure(
        paper_id, figure_type, caption, ["测试"],
        {"source": "pdf_extract", "page": page},
        ".png", PNG_HEAD + marker)


def _enable_vision(monkeypatch, analysis: dict | None = None):
    """把视觉可用性固定为"已启用"，并把网络调用打桩成固定分析结果。"""
    monkeypatch.setattr(vision, "is_enabled", lambda: True)
    monkeypatch.setattr(vision, "resolve_config", lambda: {
        "enabled": True, "base_url": "https://api.example.com/v1",
        "api_key": "sk-test", "model": "qwen-vl-max", "inherits_main": False})
    payload = dict(analysis or ANALYSIS)

    def fake_analyze(data, mime="image/png", hint="", max_tokens=None):
        if b"BOOM" in data:                     # 模拟模型侧失败
            return {"ok": False, "error": "HTTP 500：模型服务不可用", "metrics": [],
                    "description": "", "technique": "", "confidence": "", "notes": ""}
        if b"EMPTY" in data:                    # 模拟模型没给出描述
            return {"ok": True, "error": None, "figure_type": "spectra",
                    "technique": "PXRD", "description": "", "metrics": [],
                    "confidence": "", "notes": ""}
        return dict(payload)

    monkeypatch.setattr(vision, "analyze_image", fake_analyze)


# ---------------------------------------------------------------- 1. 数据层

def test_set_figure_caption_writes_and_merges_meta(conn):
    rec = add_fig(page=3, caption="原文图注：PXRD 图谱")
    row = library_db.mirror_figure(conn, rec)
    assert row is not None and row["caption_source"] == "pdf"
    assert row["meta"]["source"] == "pdf_extract" and row["meta"]["page"] == 3

    updated = library_db.set_figure_caption(
        conn, rec["fig_id"], "图 1：COF 膜的 PXRD 图谱", source="vision",
        technique="PXRD",
        metrics=[{"name": "2θ", "value": 4.7, "unit": "°"},
                 {"name": "", "value": 1, "unit": "x"},      # 无名指标丢弃
                 {"name": "垃圾", "value": "abc", "unit": ""}],  # 非数值丢弃
        notes="峰位读数清晰", confidence="HIGH")
    assert updated is not None
    # 1) caption / source / 顶层便捷字段
    assert updated["caption"] == "图 1：COF 膜的 PXRD 图谱"
    assert updated["caption_source"] == "vision"
    assert updated["fig_id"] == rec["fig_id"] and updated["paper_id"] == "1"
    assert updated["page"] == 3 and updated["technique"] == "PXRD"
    # 2) meta_json 合并：已有键（source/page）保留，新键写入
    assert updated["meta"]["source"] == "pdf_extract"
    assert updated["meta"]["page"] == 3
    assert updated["meta"]["technique"] == "PXRD"
    assert updated["meta"]["confidence"] == "high"
    assert updated["meta"]["notes"] == "峰位读数清晰"
    assert [m["name"] for m in updated["meta"]["metrics"]] == ["2θ"]
    # 3) 旧图注不丢（留档，便于人工复核/回退）
    assert updated["meta"]["caption_prev"] == "原文图注：PXRD 图谱"
    # 4) 落盘（重新查库确认，不是内存态）
    again = library_db.get_figure(conn, rec["fig_id"])
    assert again["caption_source"] == "vision" and again["caption"].startswith("图 1")

    # 5) 只传部分字段时，未传的附加信息不被清掉
    only_notes = library_db.set_figure_caption(conn, rec["fig_id"], "改写图注",
                                              source="user", notes="人工修订")
    assert only_notes["caption_source"] == "user"
    assert only_notes["meta"]["technique"] == "PXRD"
    assert len(only_notes["meta"]["metrics"]) == 1


def test_set_figure_caption_rejects_bad_source(conn):
    rec = add_fig()
    library_db.mirror_figure(conn, rec)
    with pytest.raises(ValueError):
        library_db.set_figure_caption(conn, rec["fig_id"], "x", source="llm")
    with pytest.raises(ValueError):
        library_db.set_figure_caption(conn, rec["fig_id"], "x", source="")
    # 合法值不抛
    for src in library_db.ALLOWED_CAPTION_SOURCES:
        assert library_db.set_figure_caption(
            conn, rec["fig_id"], f"图注-{src}", source=src)["caption_source"] == src


def test_set_figure_caption_missing_figure_returns_none(conn):
    assert library_db.set_figure_caption(conn, "fig_not_exist", "x") is None
    assert library_db.get_figure(conn, "fig_not_exist") is None


def test_get_and_list_figures_filter_and_page_order(conn):
    a = add_fig(page=2, marker=b"a")
    b = add_fig(page=1, marker=b"b")
    c = add_fig(page=3, marker=b"c")
    other = add_fig(paper_id="2", page=1, marker=b"d")
    for rec in (a, b, c, other):
        library_db.mirror_figure(conn, rec)
    rows = library_db.list_figures(conn, "1")
    assert [r["fig_id"] for r in rows] == [b["fig_id"], a["fig_id"], c["fig_id"]]
    assert all(r["paper_id"] == "1" for r in rows)
    assert len(library_db.list_figures(conn, "1", limit=2)) == 2
    assert [r["fig_id"] for r in library_db.list_figures(conn, "2")] == [other["fig_id"]]
    # 无页码的排最后
    no_page = figures.add_figure("1", "spectra", "无页码", [], {}, ".png",
                                 PNG_HEAD + b"e")
    library_db.mirror_figure(conn, no_page)
    assert library_db.list_figures(conn, "1")[-1]["fig_id"] == no_page["fig_id"]


def test_mirror_figure_is_idempotent_and_keeps_captions(conn):
    rec = add_fig()
    assert library_db.mirror_figure(conn, rec)["caption_source"] == "pdf"
    library_db.set_figure_caption(conn, rec["fig_id"], "视觉图注", source="vision",
                                  technique="PXRD")
    again = library_db.mirror_figure(conn, rec)      # 再次镜像不得冲掉标注
    assert again["caption"] == "视觉图注" and again["caption_source"] == "vision"


# ---------------------------------------------------------------- 2. caption_figure

def test_caption_figure_disabled_returns_error_without_raising(monkeypatch):
    monkeypatch.setattr(vision, "is_enabled", lambda: False)
    monkeypatch.setattr(vision, "resolve_config", lambda: {
        "enabled": False, "base_url": "", "api_key": "", "model": "",
        "inherits_main": True})

    def boom(*_a, **_k):  # 未启用时**不允许**发生任何网络/模型调用
        raise AssertionError("未启用视觉时不应调用视觉模型")

    monkeypatch.setattr(vision, "analyze_image", boom)
    out = vision.caption_figure(data=PNG_HEAD + b"x")
    assert out["ok"] is False
    assert out["error"] == "视觉模型未启用或配置不完整"
    assert out["caption"] == "" and out["metrics"] == []
    # 路径形式同样不抛异常
    out2 = vision.caption_figure(path=Path("no_such_file.png"))
    assert out2["ok"] is False and out2["error"]


def test_caption_figure_builds_caption_from_model_output_only(monkeypatch):
    _enable_vision(monkeypatch)
    out = vision.caption_figure(data=PNG_HEAD + b"A", mime="image/png",
                                hint="只看 PXRD")
    assert out["ok"] is True and out["error"] is None
    assert out["caption"].startswith("该图为 COF 膜的 PXRD 图谱")
    assert "关键数值" in out["caption"]
    assert "2θ 4.7 °" in out["caption"] and "2θ 27.1 °" in out["caption"]
    assert out["technique"] == "PXRD" and out["confidence"] == "high"
    assert out["notes"] == "峰位读数清晰" and out["figure_type"] == "spectra"
    # 只包含模型给的数值（2 条），没有凭空多出来的数
    assert len(out["metrics"]) == 2 and out["metrics"][0]["value"] == 4.7


def test_caption_figure_does_not_invent_metrics(monkeypatch):
    """模型没给 metrics / 没给 description → 不编造。"""
    _enable_vision(monkeypatch, {"ok": True, "error": None, "figure_type": "sem",
                                 "technique": "", "description": "SEM 形貌图",
                                 "metrics": [], "confidence": "low", "notes": ""})
    out = vision.caption_figure(data=PNG_HEAD + b"A")
    assert out["ok"] is True and out["caption"] == "SEM 形貌图"
    assert "关键数值" not in out["caption"] and out["metrics"] == []

    _enable_vision(monkeypatch, {"ok": True, "error": None, "figure_type": "",
                                 "technique": "", "description": "", "metrics": [],
                                 "confidence": "", "notes": ""})
    empty = vision.caption_figure(data=PNG_HEAD + b"A")
    assert empty["ok"] is False and empty["error"] == "视觉模型未返回可用描述"


def test_caption_metric_cap_and_length_cap(monkeypatch):
    metrics = [{"name": f"指标{i}", "value": i, "unit": "u"} for i in range(8)]
    # 1) metrics 最多 4 条进入图注（描述短，不会被长度截断干扰）
    _enable_vision(monkeypatch, {
        "ok": True, "error": None, "figure_type": "spectra", "technique": "PXRD",
        "description": "短描述", "metrics": metrics, "confidence": "high", "notes": ""})
    out = vision.caption_figure(data=PNG_HEAD + b"A")
    assert out["ok"] is True
    assert "指标3" in out["caption"] and "指标4" not in out["caption"]
    assert vision.build_caption({"description": "x", "metrics": metrics}).count("指标") == 4

    # 2) 总长 ≤ 300 字（长描述 + metrics 时截断，不超限）
    _enable_vision(monkeypatch, {
        "ok": True, "error": None, "figure_type": "spectra", "technique": "PXRD",
        "description": "很长的一段描述" * 40, "metrics": metrics,
        "confidence": "high", "notes": ""})
    long_out = vision.caption_figure(data=PNG_HEAD + b"A")
    assert long_out["ok"] is True
    assert len(long_out["caption"]) <= vision.CAPTION_MAX_CHARS == 300


def test_caption_figure_svg_and_missing_file(monkeypatch, env):
    _enable_vision(monkeypatch)
    svg = env["data"] / "literature" / "figures" / "x.svg"
    svg.parent.mkdir(parents=True, exist_ok=True)
    svg.write_text("<svg/>", encoding="utf-8")
    out = vision.caption_figure(path=svg)
    assert out["ok"] is False and "SVG" in out["error"]
    missing = vision.caption_figure(path=env["data"] / "nope.png")
    assert missing["ok"] is False and "读取图片失败" in missing["error"]


# ---------------------------------------------------------------- 3. API 端点

def test_single_caption_endpoint_writes_caption(client, monkeypatch, conn):
    rec = add_fig(page=2, caption="原文图注")
    _enable_vision(monkeypatch)
    r = client.post(f"/api/library/figures/{rec['fig_id']}/caption",
                    json={"hint": "只看 PXRD"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["figure"]["caption_source"] == "vision"
    assert "关键数值" in body["figure"]["caption"]
    assert body["analysis"]["technique"] == "PXRD"
    # 两处都写：DB 镜像 + 事实来源索引
    assert library_db.get_figure(conn, rec["fig_id"])["caption_source"] == "vision"
    figures._cache = None
    assert figures.get_figure(rec["fig_id"])["caption_source"] == "vision"
    assert figures.get_figure(rec["fig_id"])["meta"]["technique"] == "PXRD"
    # 请求体可省略（body 为空）
    r2 = client.post(f"/api/library/figures/{rec['fig_id']}/caption")
    assert r2.status_code == 200 and r2.json()["ok"] is True


def test_single_caption_endpoint_vision_disabled_and_404(client, monkeypatch):
    rec = add_fig()
    monkeypatch.setattr(vision, "is_enabled", lambda: False)
    r = client.post(f"/api/library/figures/{rec['fig_id']}/caption")
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "视觉读图" in detail and "设置" in detail and "LLM" in detail

    _enable_vision(monkeypatch)
    r404 = client.post("/api/library/figures/fig_not_exist/caption")
    assert r404.status_code == 404
    assert "不存在" in r404.json()["detail"]


def test_single_caption_endpoint_model_error_is_200_with_reason(client, monkeypatch):
    rec = add_fig(marker=b"BOOM")
    _enable_vision(monkeypatch)
    r = client.post(f"/api/library/figures/{rec['fig_id']}/caption")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False and "模型服务不可用" in body["error"]
    assert body["figure"]["caption_source"] == "pdf"      # 失败不写脏数据
    assert figures.get_figure(rec["fig_id"])["caption_source"] == "pdf"


def test_single_caption_endpoint_missing_file_is_not_500(client, monkeypatch, env):
    rec = add_fig()
    (env["data"] / "literature" / "figures" / rec["file"]).unlink()
    _enable_vision(monkeypatch)
    r = client.post(f"/api/library/figures/{rec['fig_id']}/caption")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False and "不存在或不可读" in body["error"]


def test_batch_endpoint_skips_done_and_force_redoes(client, monkeypatch, conn):
    pdf_fig = add_fig(page=1, caption="图 1：PXRD", marker=b"pdf")
    user_fig = add_fig(page=2, caption="图 2：人工改过", marker=b"user")
    vision_fig = add_fig(page=3, caption="图 3：早先读过", marker=b"vision")
    figures.update_figure(user_fig["fig_id"], caption="图 2：人工改过",
                          caption_source="user")
    figures.update_figure(vision_fig["fig_id"], caption="图 3：早先读过",
                          caption_source="vision")
    _enable_vision(monkeypatch)

    r = client.post("/api/library/papers/1/figures/caption", json={"limit": 10})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["requested"] == 3
    assert body["skipped"] == 2 and set(body["skipped_fig_ids"]) == {
        user_fig["fig_id"], vision_fig["fig_id"]}
    assert body["captioned"] == 1 and body["failed"] == 0 and body["errors"] == []
    assert body["figures"][0]["fig_id"] == pdf_fig["fig_id"]
    assert body["figures"][0]["caption_source"] == "vision"
    # 人工标注不被覆盖
    figures._cache = None
    assert figures.get_figure(user_fig["fig_id"])["caption"] == "图 2：人工改过"

    r2 = client.post("/api/library/papers/1/figures/caption",
                     json={"limit": 10, "force": True})
    body2 = r2.json()
    assert body2["captioned"] == 3 and body2["skipped"] == 0
    assert library_db.get_figure(conn, user_fig["fig_id"])["caption_source"] == "vision"

    # 请求体可省略（用默认 limit=10 / force=false）
    r3 = client.post("/api/library/papers/1/figures/caption")
    assert r3.status_code == 200
    assert r3.json()["skipped"] == 3 and r3.json()["captioned"] == 0


def test_batch_endpoint_limit_and_partial_failure(client, monkeypatch, env):
    figs = [add_fig(page=i + 1, caption=f"图 {i + 1}", marker=f"F{i}".encode())
            for i in range(5)]
    # 两张会失败：一张文件缺失（第 4 页）、一张模型报错（第 6 页）
    (env["data"] / "literature" / "figures" / figs[3]["file"]).unlink()
    boom = add_fig(page=6, caption="图 6", marker=b"BOOM")
    _enable_vision(monkeypatch)

    r = client.post("/api/library/papers/1/figures/caption",
                    json={"limit": 2, "force": True})
    body = r.json()
    assert body["requested"] == 6 and body["limit"] == 2
    assert body["captioned"] == 2 and body["failed"] == 0     # limit 生效（前 2 页）
    assert [f["fig_id"] for f in body["figures"]] == [figs[0]["fig_id"], figs[1]["fig_id"]]

    r2 = client.post("/api/library/papers/1/figures/caption",
                     json={"limit": 50, "force": True})
    body2 = r2.json()
    assert body2["captioned"] == 4 and body2["failed"] == 2
    errs = {e["fig_id"]: e["error"] for e in body2["errors"]}
    assert "不存在或不可读" in errs[figs[3]["fig_id"]]
    assert "模型服务不可用" in errs[boom["fig_id"]]
    assert len(body2["figures"]) == 4

    # fig_ids 只挑指定图
    r3 = client.post("/api/library/papers/1/figures/caption",
                     json={"fig_ids": [figs[0]["fig_id"]], "force": True})
    assert r3.json()["requested"] == 1 and r3.json()["captioned"] == 1


def test_batch_endpoint_vision_disabled_is_409(client, monkeypatch):
    add_fig()
    monkeypatch.setattr(vision, "is_enabled", lambda: False)
    r = client.post("/api/library/papers/1/figures/caption", json={"limit": 3})
    assert r.status_code == 409
    assert "视觉读图" in r.json()["detail"]


# ---------------------------------------------------------------- 4. 助手工具

def test_view_figure_registered_with_full_schema():
    assert "view_figure" in registry.TOOLS
    spec = registry.TOOLS["view_figure"]
    fn = spec["schema"]["function"]
    assert fn["name"] == "view_figure"
    props = fn["parameters"]["properties"]
    assert set(props) == {"mode", "paper_id", "fig_id", "save_caption", "hint"}
    assert props["mode"]["enum"] == ["list", "read"]
    assert "费用" in fn["description"]          # 明确提示可能产生 API 费用
    assert "读图" in fn["description"]
    assert fn["parameters"]["required"] == []
    names = {t["function"]["name"] for t in registry.list_tool_schemas()}
    assert "view_figure" in names and "view_figure" in registry.describe_tools()
    assert callable(spec["confirm"])


def test_view_figure_list_mode(env):
    a = add_fig(page=2, caption="图 2：N2 吸附等温线", marker=b"a")
    b = add_fig(page=1, caption="图 1：PXRD 图谱", marker=b"b")
    from literature import library_db as db_mod
    db_mod.ensure_ready()
    conn = db_mod.connect(env["data"] / "literature" / "library.db")
    try:
        db_mod.mirror_figure(conn, a)
        db_mod.set_figure_caption(conn, a["fig_id"], "视觉图注：等温线", source="vision",
                                  technique="BET")
    finally:
        conn.close()
    figures._cache = None

    out = litfigure.view_figure({"mode": "list", "paper_id": "1"})
    assert out["ok"] is True and out["count"] == 2
    assert [f["fig_id"] for f in out["figures"]] == [b["fig_id"], a["fig_id"]]  # 按页
    first = out["figures"][0]
    assert set(first) == {"fig_id", "page", "caption", "caption_source", "technique"}
    assert first["page"] == 1 and first["caption_source"] == "pdf"
    assert out["figures"][1]["caption_source"] == "vision"
    assert out["figures"][1]["technique"] == "BET"
    assert "文献 1 共有 2 张图" in out["text"]

    # 无 paper_id → 回最近入库的图并提示补参数
    recent = litfigure.view_figure({"mode": "list"})
    assert recent["ok"] is True and recent["count"] == 2
    assert "paper_id" in recent["text"]

    # 文献没有图 → 如实失败
    none = litfigure.view_figure({"mode": "list", "paper_id": "2"})
    assert none["ok"] is False and "没有" in none["reason"]


def test_view_figure_read_without_vision_tells_truth(env, monkeypatch):
    rec = add_fig()
    monkeypatch.setattr(vision, "is_enabled", lambda: False)
    out = litfigure.view_figure({"mode": "read", "fig_id": rec["fig_id"]})
    assert out["ok"] is False
    assert "视觉读图未启用" in out["reason"] or "未启用" in out["reason"]
    assert "设置" in out["suggestion"] and "视觉读图" in out["suggestion"]
    assert "无法真正看到" in out["text"]      # 如实说明没看到图
    # 通过 registry 执行也一样（handler 契约：text/details/is_error）
    res = registry.execute("view_figure", {"mode": "read", "fig_id": rec["fig_id"]})
    assert res["is_error"] is False and "视觉读图未启用" in res["text"]
    assert res["details"]["ok"] is False


def test_view_figure_read_and_save_caption(env, monkeypatch):
    rec = add_fig(page=4, caption="图 4：PXRD")
    _enable_vision(monkeypatch)

    out = litfigure.view_figure({"mode": "read", "fig_id": rec["fig_id"]})
    assert out["ok"] is True
    assert out["fig_id"] == rec["fig_id"] and out["page"] == 4
    assert out["technique"] == "PXRD" and out["confidence"] == "high"
    assert out["description"] == "该图为 COF 膜的 PXRD 图谱"
    assert len(out["metrics"]) == 2 and out["notes"] == "峰位读数清晰"
    assert "关键数值" in out["caption"]
    assert out["saved"] is False and "未写入标注" in out["message"]
    figures._cache = None
    assert figures.get_figure(rec["fig_id"])["caption_source"] == "pdf"  # 未写

    saved = litfigure.view_figure({"mode": "read", "fig_id": rec["fig_id"],
                                   "save_caption": True})
    assert saved["ok"] is True and saved["saved"] is True
    assert "已写入视觉标注" in saved["message"]
    figures._cache = None
    assert figures.get_figure(rec["fig_id"])["caption_source"] == "vision"
    from literature import library_db as db_mod
    conn = db_mod.connect(env["data"] / "literature" / "library.db")
    try:
        row = db_mod.get_figure(conn, rec["fig_id"])
    finally:
        conn.close()
    assert row["caption_source"] == "vision" and row["meta"]["technique"] == "PXRD"

    # 按 paper_id + hint 关键词挑图
    add_fig(page=5, caption="图 5：截留率与通量", marker=b"z")
    figures._cache = None
    picked = litfigure.view_figure({"mode": "read", "paper_id": "1",
                                    "hint": "截留率"})
    assert picked["ok"] is True and picked["page"] == 5
    missed = litfigure.view_figure({"mode": "read", "paper_id": "1",
                                    "hint": "不存在的关键词"})
    assert missed["ok"] is False and "匹配" in missed["reason"]
    assert litfigure.view_figure({"mode": "read"})["ok"] is False


def test_view_figure_confirm_only_when_writing():
    assert registry.confirm_impact("view_figure", {"mode": "list"}) is None
    assert registry.confirm_impact("view_figure",
                                   {"mode": "read", "save_caption": False}) is None
    impact = registry.confirm_impact("view_figure",
                                     {"mode": "read", "save_caption": True})
    assert impact and "费用" in impact and "继续" in impact


def test_view_figure_rejects_bad_mode():
    out = litfigure.view_figure({"mode": "bogus"})
    assert out["ok"] is False and out["is_error"] is True and "list" in out["suggestion"]
    assert registry.execute("view_figure", {"mode": "bogus"})["is_error"] is True


def test_no_test_writes_into_repo_or_real_user_dir(env):
    """红线：所有落盘都在 tmp_path，仓库 data/ 与真实用户目录不被触碰。"""
    add_fig()
    data = env["data"]
    assert str(data).startswith(str(env["tmp"]))
    assert (data / "literature" / "figures_index.json").is_file()
    repo_data = PROJECT_ROOT / "data" / "literature"
    if (repo_data / "figures_index.json").exists():
        recs = json.loads((repo_data / "figures_index.json").read_text(encoding="utf-8"))
        assert all("视觉标注测试文献" not in json.dumps(r, ensure_ascii=False)
                   for r in recs), "测试记录不得写入仓库 data/"


def test_figure_caption_module_helpers(env):
    """共享流水线辅助：来源判定优先级 + 页码解析 + 文件解析。"""
    rec = add_fig(page=7)
    assert figure_caption.figure_page(rec) == 7
    assert figure_caption.figure_page({"meta": {"page": "9"}}) == 9
    assert figure_caption.figure_page({}) is None
    assert figure_caption.effective_caption_source(rec) == "pdf"
    assert figure_caption.effective_caption_source(
        {"caption": "x", "caption_source": "none"}, {"caption_source": "vision"}) == "vision"
    assert figure_caption.effective_caption_source({"caption": ""}, None) == "none"
    path = figures.resolve_figure_file(rec)
    assert path is not None and path.name == rec["file"]
    assert figures.resolve_figure_file({"file": "nope.png"}) is None
    data, mime, err = figure_caption.read_figure_bytes(rec)
    assert err is None and data.startswith(PNG_HEAD) and mime == "image/png"


def test_index_caption_source_semantics(env):
    """索引侧来源约定：PDF 抽取→pdf；人工上传填图注→user；无图注→none；改文本→user。"""
    pdf_fig = add_fig(caption="图 1：原文")                      # meta.source=pdf_extract
    blank = figures.add_figure("1", "spectra", "", [], {}, ".png", PNG_HEAD + b"n")
    manual = figures.add_figure("1", "spectra", "我写的图注", [], {}, ".png",
                                PNG_HEAD + b"m")
    assert pdf_fig["caption_source"] == "pdf"
    assert blank["caption_source"] == "none"
    assert manual["caption_source"] == "user"
    # 视觉标注写回后再由人工改文本 → user；只改标签 → 保留 vision
    figures.update_figure(pdf_fig["fig_id"], caption="视觉图注", caption_source="vision")
    same = figures.update_figure(pdf_fig["fig_id"], caption="视觉图注", tags=["只改标签"])
    assert same["caption_source"] == "vision"
    changed = figures.update_figure(pdf_fig["fig_id"], caption="人工改写")
    assert changed["caption_source"] == "user"
    with pytest.raises(figures.FigureError):
        figures.update_figure(pdf_fig["fig_id"], caption_source="llm")
