"""v2.0.2（需求 1）：文献图「AI 识别」端点前缀统一 —— `/api/literature/...` 为 canonical。

覆盖：
1. 两条路径**都在**（新 canonical + 旧 `/api/library/...` 保留），且注册在同一实现上
   （打桩 `api.routers.library.caption_figure_response` /
   `caption_paper_figures_response` 后，两条路径都必须命中该打桩 —— 这是"实现只有一份"
   的机器可验证证据，而不是靠读代码判断）；
2. 同一张图经两条路径结果一致（ok / caption / analysis / figure.caption_source）；
3. 关键分支等价：视觉未启用 → 409（中文提示一致）、图不存在 → 404、模型报错/文件缺失
   → 200 + ok:false + 中文 error（两条路径逐项比对）；
4. 批量端点在新前缀下的跳过口径（`caption_source ∈ {vision, user}` 跳过、`force` 重做、
   `limit` 生效、`fig_ids` 过滤、单张失败不影响其余）。

隔离与 `tests/test_figure_caption_v201.py` 同源：runtime_config 数据根、figures 目录/索引、
library_db 路径、titles 路径全部 monkeypatch 到 tmp_path；**视觉调用一律打桩**，不联网。
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

from api.routers import library as library_router  # noqa: E402
from api.routers import literature as literature_router  # noqa: E402
from literature import figures, library_db, vision  # noqa: E402

#: 同一语义的两条路径（新 = canonical，旧 = 历史，必须都可用且等价）
SINGLE_PATHS = ("/api/literature/figures/{fig_id}/caption",
                "/api/library/figures/{fig_id}/caption")
BATCH_PATHS = ("/api/literature/papers/{paper_id}/figures/caption",
               "/api/library/papers/{paper_id}/figures/caption")

PAPERS = {
    "1": {"title": "端点统一测试文献", "doi": "10.1000/v202a", "source": "builtin"},
    "2": {"title": "另一篇", "doi": "10.1000/v202b", "source": "builtin"},
}
PNG_HEAD = b"\x89PNG\r\n\x1a\n"

ANALYSIS = {
    "ok": True, "error": None, "figure_type": "spectra", "technique": "PXRD",
    "description": "该图为 COF 膜的 PXRD 图谱",
    "metrics": [{"name": "2θ", "value": 4.7, "unit": "°"}],
    "confidence": "high", "notes": "峰位读数清晰",
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

    monkeypatch.setattr(figures, "FIGURES_DIR", data / "literature" / "figures")
    monkeypatch.setattr(figures, "INDEX_PATH",
                        data / "literature" / "figures_index.json")
    monkeypatch.setattr(figures, "_cache", None)
    monkeypatch.setattr(library_db, "db_path",
                        lambda: data / "literature" / "library.db")
    monkeypatch.setenv("COF_DATA_DIR", str(data))

    try:
        from references import titles as titles_mod
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
            marker: bytes = b"A") -> dict:
    return figures.add_figure(
        paper_id, "spectra", caption, ["测试"],
        {"source": "pdf_extract", "page": page}, ".png", PNG_HEAD + marker)


def _enable_vision(monkeypatch, analysis: dict | None = None):
    """视觉可用性固定为"已启用"，网络调用打桩（测试里绝不出网）。"""
    monkeypatch.setattr(vision, "is_enabled", lambda: True)
    monkeypatch.setattr(vision, "resolve_config", lambda: {
        "enabled": True, "base_url": "https://api.example.com/v1",
        "api_key": "sk-test", "model": "qwen-vl-max", "inherits_main": False})
    payload = dict(analysis or ANALYSIS)

    def fake_analyze(data, mime="image/png", hint="", max_tokens=None):
        if b"BOOM" in data:
            return {"ok": False, "error": "HTTP 500：模型服务不可用", "metrics": [],
                    "description": "", "technique": "", "confidence": "", "notes": ""}
        return dict(payload)

    monkeypatch.setattr(vision, "analyze_image", fake_analyze)


# ---------------------------------------------------------------- 1. 路由 + 单一实现

def test_new_paths_exist_in_literature_router():
    """canonical 路径必须注册在**文献路由**下（前缀 /api/literature）。"""
    paths = {getattr(r, "path", "") for r in literature_router.router.routes}
    for tpl in SINGLE_PATHS[:1] + BATCH_PATHS[:1]:
        assert tpl in paths, f"缺少 canonical 路由 {tpl}"
    # 旧路径仍在文献库路由下（未删除）
    legacy = {getattr(r, "path", "") for r in library_router.router.routes}
    assert SINGLE_PATHS[1] in legacy and BATCH_PATHS[1] in legacy


def test_both_paths_hit_the_same_implementation(client, monkeypatch):
    """两条路径都转发到 `library.caption_*_response` —— 实现只有一份的机器验证。"""
    rec = add_fig()
    _enable_vision(monkeypatch)
    calls: list[tuple] = []

    def fake_single(fig_id, req=None):
        calls.append(("single", fig_id, None if req is None else req.hint))
        return {"ok": True, "fig_id": fig_id, "figure": {"fig_id": fig_id},
                "analysis": None, "caption": "打桩"}

    def fake_batch(paper_id, req=None):
        calls.append(("batch", paper_id, None if req is None else req.limit))
        return {"ok": True, "paper_id": paper_id, "requested": 0, "captioned": 0,
                "skipped": 0, "failed": 0, "errors": [], "figures": []}

    monkeypatch.setattr(library_router, "caption_figure_response", fake_single)
    monkeypatch.setattr(library_router, "caption_paper_figures_response", fake_batch)

    for tpl in SINGLE_PATHS:
        r = client.post(tpl.format(fig_id=rec["fig_id"]), json={"hint": "只看 PXRD"})
        assert r.status_code == 200, (tpl, r.text)
        assert r.json()["caption"] == "打桩"
    for tpl in BATCH_PATHS:
        r = client.post(tpl.format(paper_id="1"), json={"limit": 3})
        assert r.status_code == 200, (tpl, r.text)

    assert calls == [("single", rec["fig_id"], "只看 PXRD"),
                     ("single", rec["fig_id"], "只看 PXRD"),
                     ("batch", "1", 3), ("batch", "1", 3)]


# ---------------------------------------------------------------- 2. 两条路径结果一致

def test_single_caption_same_result_on_both_paths(client, monkeypatch, conn):
    rec = add_fig(page=2, caption="原文图注")
    _enable_vision(monkeypatch)

    bodies = []
    for tpl in SINGLE_PATHS:
        r = client.post(tpl.format(fig_id=rec["fig_id"]), json={"hint": "只看 PXRD"})
        assert r.status_code == 200, (tpl, r.text)
        bodies.append(r.json())

    new, old = bodies
    assert new["ok"] is True and old["ok"] is True
    assert new["fig_id"] == old["fig_id"] == rec["fig_id"]
    assert new["caption"] == old["caption"] and "关键数值" in new["caption"]
    assert new["analysis"] == old["analysis"]
    assert new["figure"]["caption_source"] == old["figure"]["caption_source"] == "vision"
    assert new["figure"]["caption"] == old["figure"]["caption"]

    # 请求体可省略（空 body）也要两边都成立
    for tpl in SINGLE_PATHS:
        r = client.post(tpl.format(fig_id=rec["fig_id"]))
        assert r.status_code == 200 and r.json()["ok"] is True
    # 两处都写：DB 镜像 + 事实来源索引
    assert library_db.get_figure(conn, rec["fig_id"])["caption_source"] == "vision"
    figures._cache = None
    assert figures.get_figure(rec["fig_id"])["caption_source"] == "vision"


def test_single_caption_error_branches_equivalent(client, monkeypatch, env):
    """409（未启用）/ 404（图不存在）/ 200+ok:false（模型报错、文件缺失）两路径等价。"""
    rec = add_fig()
    boom = add_fig(page=3, marker=b"BOOM")
    gone = add_fig(page=4, marker=b"gone")

    # 1) 视觉未启用 → 409 且提示一致
    monkeypatch.setattr(vision, "is_enabled", lambda: False)
    details = []
    for tpl in SINGLE_PATHS:
        r = client.post(tpl.format(fig_id=rec["fig_id"]))
        assert r.status_code == 409, (tpl, r.text)
        details.append(r.json()["detail"])
    assert details[0] == details[1]
    assert "视觉读图" in details[0] and "设置" in details[0]

    _enable_vision(monkeypatch)

    # 2) 图不存在 → 404，detail 一致
    details = []
    for tpl in SINGLE_PATHS:
        r = client.post(tpl.format(fig_id="fig_not_exist"))
        assert r.status_code == 404, (tpl, r.text)
        details.append(r.json()["detail"])
    assert details[0] == details[1] and "不存在" in details[0]

    # 3) 模型报错 → 200 + ok:false + 中文原因；失败不写脏数据
    for tpl in SINGLE_PATHS:
        r = client.post(tpl.format(fig_id=boom["fig_id"]))
        assert r.status_code == 200
        assert r.json()["ok"] is False
        assert "模型服务不可用" in r.json()["error"]
    figures._cache = None
    assert figures.get_figure(boom["fig_id"])["caption_source"] == "pdf"

    # 4) 图片文件缺失 → 200 + ok:false + 中文原因（不 500）
    (env["data"] / "literature" / "figures" / gone["file"]).unlink()
    for tpl in SINGLE_PATHS:
        r = client.post(tpl.format(fig_id=gone["fig_id"]))
        assert r.status_code == 200
        assert r.json()["ok"] is False
        assert "不存在或不可读" in r.json()["error"]


# ---------------------------------------------------------------- 3. 批量端点（新前缀）

def test_batch_endpoint_on_new_prefix_skips_done_and_force_redoes(client, monkeypatch,
                                                                  conn):
    pdf_fig = add_fig(page=1, caption="图 1：PXRD", marker=b"pdf")
    user_fig = add_fig(page=2, caption="图 2：人工改过", marker=b"user")
    vision_fig = add_fig(page=3, caption="图 3：早先读过", marker=b"vision")
    figures.update_figure(user_fig["fig_id"], caption="图 2：人工改过",
                          caption_source="user")
    figures.update_figure(vision_fig["fig_id"], caption="图 3：早先读过",
                          caption_source="vision")
    _enable_vision(monkeypatch)
    url = BATCH_PATHS[0].format(paper_id="1")

    r = client.post(url, json={"limit": 10})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["requested"] == 3
    assert body["skipped"] == 2 and set(body["skipped_fig_ids"]) == {
        user_fig["fig_id"], vision_fig["fig_id"]}
    assert body["captioned"] == 1 and body["failed"] == 0
    assert body["figures"][0]["fig_id"] == pdf_fig["fig_id"]
    assert body["figures"][0]["caption_source"] == "vision"
    # 人工标注不被覆盖
    figures._cache = None
    assert figures.get_figure(user_fig["fig_id"])["caption"] == "图 2：人工改过"

    body2 = client.post(url, json={"limit": 10, "force": True}).json()
    assert body2["captioned"] == 3 and body2["skipped"] == 0
    assert library_db.get_figure(conn, user_fig["fig_id"])["caption_source"] == "vision"

    # 请求体可省略（默认 limit=10 / force=false）
    body3 = client.post(url).json()
    assert body3["skipped"] == 3 and body3["captioned"] == 0


def test_batch_endpoint_on_new_prefix_isolates_failures(client, monkeypatch, env):
    figs = [add_fig(page=i + 1, caption=f"图 {i + 1}", marker=f"F{i}".encode())
            for i in range(4)]
    (env["data"] / "literature" / "figures" / figs[2]["file"]).unlink()   # 文件缺失
    boom = add_fig(page=6, caption="图 6", marker=b"BOOM")                # 模型报错
    _enable_vision(monkeypatch)
    url = BATCH_PATHS[0].format(paper_id="1")

    body = client.post(url, json={"limit": 50, "force": True}).json()
    assert body["captioned"] == 3 and body["failed"] == 2
    errs = {e["fig_id"]: e["error"] for e in body["errors"]}
    assert "不存在或不可读" in errs[figs[2]["fig_id"]]
    assert "模型服务不可用" in errs[boom["fig_id"]]
    assert len(body["figures"]) == 3            # 单张失败不影响其余

    # limit 只作用于真正要处理的图；fig_ids 可指定
    limited = client.post(url, json={"limit": 2, "force": True}).json()
    assert limited["limit"] == 2 and limited["captioned"] == 2
    picked = client.post(url, json={"fig_ids": [figs[0]["fig_id"]],
                                    "force": True}).json()
    assert picked["requested"] == 1 and picked["captioned"] == 1

    # 视觉未启用 → 409（两条路径一致）
    monkeypatch.setattr(vision, "is_enabled", lambda: False)
    for tpl in BATCH_PATHS:
        r = client.post(tpl.format(paper_id="1"), json={"limit": 3})
        assert r.status_code == 409 and "视觉读图" in r.json()["detail"]


def test_batch_same_result_on_both_paths(client, monkeypatch):
    """同一批图两条路径的返回结构一致（跳过计数 / 成功图集）。"""
    figs = [add_fig(page=i + 1, caption=f"图 {i + 1}", marker=f"P{i}".encode())
            for i in range(3)]
    figures.update_figure(figs[0]["fig_id"], caption="图 1（人工）",
                          caption_source="user")
    _enable_vision(monkeypatch)

    outs = []
    for tpl in BATCH_PATHS:
        body = client.post(tpl.format(paper_id="1"),
                           json={"limit": 10, "force": True}).json()
        outs.append(body)
    new, old = outs
    assert new["requested"] == old["requested"] == 3
    assert new["captioned"] == old["captioned"] == 3
    assert new["failed"] == old["failed"] == 0
    assert new["skipped"] == old["skipped"] == 0
    expected_ids = [f["fig_id"] for f in figs]          # 按 page 升序 = figs 顺序
    assert [f["fig_id"] for f in new["figures"]] == expected_ids
    assert [f["fig_id"] for f in old["figures"]] == expected_ids
    assert all(f["caption_source"] == "vision" for f in new["figures"])
    assert new["figures"] == old["figures"]
