"""P6 端到端验证：真实图片 + 真实 HTTP 层 + 真实设置文件 + 假视觉端点。

与单元测试的区别（为什么值得单独跑）：
- 单元测试 monkeypatch 掉 analyze_image；这里**不 patch 任何业务函数**，
  走完整链路：设置文件 → vision.resolve_config → base64 → HTTP → JSON 解析
  → caption 拼接 → 双写（figures_index.json + library.db 镜像）；
- 图片用你安装版里的**真实文献图**（只读复制，不碰用户数据）；
- 上传与标注都走**真实 API**（与用户点击路径一致，含共享内存缓存）；
- 覆盖成功 / 批量跳过 / force / 端点报错 / 未启用视觉 五条路径。

运行：E:\\ANACONDA\\python.exe scripts\\e2e_vision_caption.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(r"C:\Users\ckx\Desktop\全新机器学习实验")
REAL_FIGS = Path(r"C:\Users\ckx\AppData\Roaming\COF-Film-Recommend\data\literature\figures")
MOCK_OK = "http://127.0.0.1:18899/v1"      # 假模型：返回固定 PXRD 分析
MOCK_FAIL = "http://127.0.0.1:18900/v1"    # 假模型：一律 500

TMP = Path(tempfile.mkdtemp(prefix="cof_e2e_vision_"))
os.environ["COF_DATA_DIR"] = str(TMP)
for p in (str(ROOT), str(ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

FAILS: list[str] = []
DATA_ROOT = TMP / "data"           # COF_DATA_DIR 下 user_data_root() = <dir>/data
SETTINGS = TMP / "config" / "literature_llm.local.json"


def check(cond: bool, label: str, extra: str = "") -> None:
    print(f"  [{'OK ' if cond else 'FAIL'}] {label}" + (f"  {extra}" if extra else ""))
    if not cond:
        FAILS.append(label)


def patch_all(suffix: str, attr: str, value) -> int:
    """同名模块可能以 `x` 与 `src.x` 两种身份同时在 sys.modules —— 全部打桩。

    （第一次跑只打桩了其中一个实例，结果 vision 读的是另一个实例的真实设置，
    表现为「明明配了却说没配」；这类双实例陷阱在本仓库很常见。）
    """
    n = 0
    for name, mod in list(sys.modules.items()):
        if (name == suffix or name.endswith("." + suffix)) and hasattr(mod, attr):
            setattr(mod, attr, value)
            n += 1
    return n


def main() -> int:
    print(f"临时数据目录 = {TMP}（安装版真实图片只读引用）")

    # ---------- 1) 设置链路 ----------
    from src.literature import llm_extract, vision
    n = patch_all("llm_extract", "SETTINGS_PATH", SETTINGS)
    llm_extract.save_settings(enabled=True, base_url=MOCK_OK, api_key="sk-mock",
                              model="mock-text", vision_enabled=True,
                              vision_base_url=MOCK_OK, vision_api_key="sk-mock",
                              vision_model="mock-vl")
    print(f"\n1) 设置链路（打桩 {n} 个 llm_extract 实例的 SETTINGS_PATH）")
    cfg = vision.resolve_config()
    check(vision.is_enabled(), "vision.is_enabled() 为真", f"model={cfg['model']}")
    check(cfg["base_url"] == MOCK_OK, "端点来自设置文件（未错误回退主配置）")

    # ---------- 2) 走真实上传接口 ----------
    print("\n2) 真实上传接口 POST /api/literature/1/figures（素材：安装版真实文献图）")
    from fastapi.testclient import TestClient
    from api.main import app
    client = TestClient(app)
    picks = sorted(REAL_FIGS.glob("*.png"))[:1] + sorted(REAL_FIGS.glob("*.jpg"))[:1]
    check(len(picks) >= 2, "取到真实图文件", f"{[p.name for p in picks]}")
    if len(picks) < 2:
        return 1
    fig_ids: list[str] = []
    for p in picks:
        mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
        r = client.post("/api/literature/1/figures",
                        files={"file": (p.name, p.read_bytes(), mime)},
                        data={"figure_type": "spectra", "caption": "",
                              "tags": "", "meta_json": json.dumps({"technique": "PXRD"})})
        ok = r.status_code == 201
        check(ok, f"上传 {p.name} → 201", f"status={r.status_code} body={str(r.text)[:80]}")
        if ok:
            fig_ids.append(r.json()["fig_id"])
    if len(fig_ids) < 2:
        return 1
    print(f"     fig_ids = {fig_ids}")

    # ---------- 3) 单图标注（成功路径） ----------
    print("\n3) 单图标注 POST /api/library/figures/{id}/caption")
    r = client.post(f"/api/library/figures/{fig_ids[0]}/caption")
    check(r.status_code == 200, "HTTP 200", f"status={r.status_code}")
    body = r.json() if r.status_code == 200 else {}
    check(body.get("ok") is True, "ok=True", f"error={body.get('error')}")
    cap = str(body.get("caption") or "")
    check("假模型" in cap, "caption 含模型描述", f"{cap[:56]}…")
    check("2θ峰位" in cap and "比表面积" in cap, "metrics 被拼进 caption", f"{cap[:90]}")
    check(cap.count("关键数值") == 1, "数值段只出现一次")

    from src.literature import library_db
    library_db.ensure_ready()
    conn = library_db.connect(library_db.db_path())
    idx_path = DATA_ROOT / "literature" / "figures_index.json"
    raw = json.loads(idx_path.read_text(encoding="utf-8"))
    items = raw if isinstance(raw, list) else (raw.get("figures") or [])
    idxrec = next((x for x in items if x.get("fig_id") == fig_ids[0]), {})
    check(idxrec.get("caption_source") == "vision", "索引（事实来源）caption_source=vision",
          f"got={idxrec.get('caption_source')}")
    check("假模型" in str(idxrec.get("caption") or ""), "索引里落盘了 caption（rebuild 不会丢）")
    row = library_db.get_figure(conn, fig_ids[0]) or {}
    check(row.get("caption_source") == "vision", "DB 镜像 caption_source=vision",
          f"got={row.get('caption_source')}")
    check((row.get("meta") or {}).get("technique") == "PXRD",
          "meta_json 合并不丢旧键", f"technique={(row.get('meta') or {}).get('technique')}")

    # ---------- 4) 批量：跳过 / limit / force ----------
    print("\n4) 批量标注 POST /api/library/papers/1/figures/caption")
    rb = client.post("/api/library/papers/1/figures/caption", json={"limit": 5})
    bb = rb.json() if rb.status_code == 200 else {}
    check(rb.status_code == 200, "HTTP 200")
    check(bb.get("skipped", 0) >= 1, "已视觉标注的被跳过", f"skipped={bb.get('skipped')}")
    check(bb.get("captioned", 0) >= 1, "其余图完成标注", f"captioned={bb.get('captioned')}")
    r2 = client.post("/api/library/papers/1/figures/caption", json={"limit": 5, "force": True})
    check(r2.status_code == 200 and (r2.json().get("captioned") or 0) >= 1,
          "force=True 会重做", f"captioned={(r2.json() or {}).get('captioned') if r2.status_code == 200 else '-'}")

    # ---------- 5) 失败路径：端点 500 → 不写库、明确报错 ----------
    print("\n5) 失败路径（假端点一律 500）")
    p = picks[0]
    r3 = client.post("/api/literature/1/figures",
                     files={"file": (p.name, p.read_bytes(), "image/png")},
                     data={"figure_type": "spectra", "caption": "", "tags": "",
                           "meta_json": json.dumps({"technique": "FTIR"})})
    fid = r3.json()["fig_id"] if r3.status_code == 201 else fig_ids[1]
    llm_extract.save_settings(vision_base_url=MOCK_FAIL)
    rf = client.post(f"/api/library/figures/{fid}/caption")
    bf = rf.json() if rf.status_code == 200 else {}
    check(rf.status_code == 200, "HTTP 200（失败也是一次正常响应）", f"status={rf.status_code}")
    check(bf.get("ok") is False, "ok=False（不假装成功）")
    check(bool(bf.get("error")), "带错误原因", f"error={str(bf.get('error'))[:70]}")
    rowf = library_db.get_figure(conn, fid) or {}
    check(rowf.get("caption_source") != "vision", "失败时未写入 vision 标注",
          f"caption_source={rowf.get('caption_source')}")

    # ---------- 6) 未启用视觉 → 409 ----------
    print("\n6) 未启用视觉（vision_enabled=False）")
    llm_extract.save_settings(vision_enabled=False)
    r409 = client.post(f"/api/library/figures/{fig_ids[0]}/caption")
    check(r409.status_code == 409, "HTTP 409", f"status={r409.status_code}")
    check(bool((r409.json() or {}).get("detail")), "detail 有中文说明",
          f"detail={str((r409.json() or {}).get('detail'))[:64]}")

    # ---------- 7) 助手工具 view_figure ----------
    print("\n7) 助手工具 view_figure（真实 settings + 假端点）")
    llm_extract.save_settings(vision_enabled=True, vision_base_url=MOCK_OK)
    from assistant import registry
    from assistant.tools import litfigure
    check("view_figure" in registry.TOOLS, "已注册到 registry.TOOLS")
    entry = registry.TOOLS.get("view_figure") or {}
    check(callable(entry.get("handler")), "handler 可调用")
    check(bool((entry.get("schema") or {}).get("function")), "schema 存在")
    out_list = litfigure.view_figure({"paper_id": "1", "mode": "list"})
    check(bool((out_list.get("details") or {}).get("figures")), "mode=list 能列出图",
          f"n={len((out_list.get('details') or {}).get('figures') or [])}")
    # 7a 只读看图（不写库）
    before = (library_db.get_figure(conn, fid) or {}).get("caption_source")
    ok_read = litfigure.view_figure({"fig_id": fid, "mode": "read"})
    d = ok_read.get("details") or {}
    check(d.get("ok") is True, "正常端点下 read 成功", f"technique={d.get('technique')}")
    check("假模型" in str(d.get("description") or ""), "返回了模型描述",
          f"desc={str(d.get('description'))[:40]}…")
    check(d.get("saved") is False, "save_caption 默认 false → 未写库")
    after = (library_db.get_figure(conn, fid) or {}).get("caption_source")
    check(after == before, "只读看图不改变标注来源", f"{before} → {after}")
    check(litfigure.confirm_impact({"mode": "read"}) is None, "只读 read 不要求二次确认")
    check(bool(litfigure.confirm_impact({"mode": "read", "save_caption": True})),
          "save_caption=true 要求二次确认")
    # 7b save_caption=true → 真正写入
    ok_save = litfigure.view_figure({"fig_id": fid, "mode": "read", "save_caption": True})
    ds = ok_save.get("details") or {}
    check(ds.get("saved") is True, "save_caption=true 写出标注")
    check((library_db.get_figure(conn, fid) or {}).get("caption_source") == "vision",
          "写库后 caption_source=vision")
    # 7c 端点故障 → 如实报错（不编造）
    llm_extract.save_settings(vision_base_url=MOCK_FAIL)
    out_bad = litfigure.view_figure({"fig_id": fid, "mode": "read"})
    db_ = out_bad.get("details") or {}
    check(db_.get("ok") is False, "端点故障时 ok=False")
    check(bool(db_.get("reason")) and bool(db_.get("suggestion")),
          "给了原因与可操作建议", f"reason={str(db_.get('reason'))[:50]}")
    check("未编造" in str(out_bad.get("text")) or "失败" in str(out_bad.get("text")),
          "文本明确说明读图失败")

    conn.close()
    if FAILS:
        print(f"\n临时目录（保留供排查）: {TMP}")
        print(f"结果 = FAIL（{len(FAILS)} 项）：" + "；".join(FAILS))
        return 1
    print(f"\n临时目录已清理: {TMP}")
    print("结果 = ALL PASS")
    return 0


if __name__ == "__main__":
    code = 2
    try:
        code = main()
    except Exception as exc:
        import traceback
        traceback.print_exc()
        print(f"\n结果 = ERROR：{type(exc).__name__}: {exc}")
    finally:
        if code == 0:
            shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(code)
