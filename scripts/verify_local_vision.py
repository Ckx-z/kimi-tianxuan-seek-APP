"""本机视觉模型（Ollama + qwen3-vl:4b）真实能力实测。

不做"本地 vs 云端"对比（用户已定：本地为主力）——本脚本只回答一个问题：
**本机模型能否真的用于「文献图片识别」与「截图识别」**，并且走的是产品同一条代码路径
（`src/literature/vision.py::analyze_image`，OpenAI 兼容 image_url + base64）。

素材：
- 2 张**真实文献图**（只读复制自安装版用户数据，不修改用户数据）
- 1 张**真实屏幕截图**（本机主屏，通过 .NET 截取；产品里由 Electron desktopCapturer 截图，
  该路径已单独真机自检 RESULT=OK）

输出：每张图的耗时、图类型、技术、描述、模型自报数值与置信度；最后 PASS/FAIL。
"""

from __future__ import annotations

import base64
import io
import json
import os
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(r"C:\Users\ckx\Desktop\全新机器学习实验")
REAL_FIGS = Path(r"C:\Users\ckx\AppData\Roaming\COF-Film-Recommend\data\literature\figures")
ENDPOINT = "http://127.0.0.1:11434/v1"
MODEL = os.environ.get("COF_LOCAL_VL_MODEL", "qwen3-vl:4b")

TMP = Path(tempfile.mkdtemp(prefix="cof_local_vl_"))
os.environ["COF_DATA_DIR"] = str(TMP)
for p in (str(REPO), str(REPO / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

FAILS: list[str] = []


def check(cond: bool, label: str, extra: str = "") -> None:
    print(f"  [{'OK ' if cond else 'FAIL'}] {label}" + (f"  {extra}" if extra else ""))
    if not cond:
        FAILS.append(label)


def capture_screen() -> bytes | None:
    """用 .NET 截主屏 → PNG bytes（产品里由 Electron desktopCapturer 负责）。"""
    try:
        import clr  # type: ignore # noqa: F401
    except Exception:
        pass
    ps = (
        "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
        "$b=[System.Windows.Forms.Screen]::PrimaryScreen.Bounds;"
        "$bmp=New-Object System.Drawing.Bitmap $b.Width,$b.Height;"
        "$g=[System.Drawing.Graphics]::FromImage($bmp);"
        "$g.CopyFromScreen($b.Location,[System.Drawing.Point]::Empty,$b.Size);"
        f"$bmp.Save('{TMP / 'screen.png'}',[System.Drawing.Imaging.ImageFormat]::Png);"
        "$g.Dispose();$bmp.Dispose()"
    )
    import subprocess
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, text=True, timeout=120)
    p = TMP / "screen.png"
    if r.returncode != 0 or not p.is_file():
        print("      截图失败:", (r.stderr or "")[:200])
        return None
    return p.read_bytes()


def chat_about_image(data: bytes, mime: str, question: str) -> dict:
    """聊天式看图（助手截图提问走的就是这条路径，不是文献图 JSON 提示词）。

    与 analyze_image 的区别：不要求输出谱图 JSON，只是把图 + 自然语言问题发给模型。
    """
    import urllib.request
    b64 = base64.b64encode(data).decode("ascii")
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": question},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
        ]}],
        "temperature": 0.2,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{ENDPOINT.rstrip('/')}/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer ollama"}, method="POST")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            data_out = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "elapsed": time.time() - t0}
    msg = ((data_out.get("choices") or [{}])[0].get("message") or {})
    text = str(msg.get("content") or "").strip()
    return {"ok": bool(text), "text": text, "elapsed": time.time() - t0,
            "error": None if text else "模型未返回内容"}


def main() -> int:
    from src.literature import llm_extract, vision

    print(f"① 配置视觉模型指向本机 Ollama（走真实 settings 文件）")
    llm_extract.SETTINGS_PATH = TMP / "config" / "literature_llm.local.json"
    # 两个模块实例都要打桩（本仓库常见陷阱）
    for name, mod in list(sys.modules.items()):
        if name.endswith("llm_extract") and hasattr(mod, "SETTINGS_PATH"):
            mod.SETTINGS_PATH = llm_extract.SETTINGS_PATH
    llm_extract.save_settings(enabled=True, base_url=ENDPOINT, api_key="ollama",
                              model=MODEL, vision_enabled=True,
                              vision_base_url=ENDPOINT, vision_api_key="ollama",
                              vision_model=MODEL)
    cfg = vision.resolve_config()
    check(vision.is_enabled(), "vision.is_enabled() 为真", f"model={cfg['model']}")
    check(cfg["base_url"] == ENDPOINT, "端点为本机 Ollama", cfg["base_url"])

    # 素材
    print("\n② 素材")
    figures = sorted(REAL_FIGS.glob("*.png"))[:1] + sorted(REAL_FIGS.glob("*.jpg"))[:1]
    print(f"      文献图: {[p.name for p in figures]}（只读引用安装版数据）")
    items: list[tuple[str, bytes, str]] = []
    for p in figures:
        mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
        items.append((f"文献图 {p.name}（{p.stat().st_size // 1024}KB）", p.read_bytes(), mime))
    shot = capture_screen()
    if shot:
        items.append((f"屏幕截图（{len(shot) // 1024}KB）", shot, "image/png"))
    check(len(items) >= 3, "三类素材齐备（2 文献图 + 1 截图）", f"n={len(items)}")

    # 逐张识别
    print("\n③ 文献图识别（产品同一代码路径 analyze_image，输出结构化图注）")
    results = []
    for label, data, mime in items:
        if label.startswith("屏幕截图"):
            continue          # 截图走聊天式路径，见 ③b
        t0 = time.time()
        res = vision.analyze_image(data, mime=mime)
        dt = time.time() - t0
        results.append((label, dt, res))
        ok = bool(res.get("ok"))
        check(ok, f"{label} → ok", f"{dt:.1f}s" + ("" if ok else f"  error={res.get('error')}"))
        if ok:
            print(f"        图类型: {res.get('figure_type') or '(未给)'}   技术: {res.get('technique') or '(未给)'}")
            print(f"        描述  : {str(res.get('description') or '')[:120]}")
            if res.get("metrics"):
                ms = "；".join(f"{m['name']} {m['value']}{m.get('unit') or ''}"
                              for m in res["metrics"][:4])
                print(f"        数值  : {ms}")
            print(f"        置信度: {res.get('confidence') or '-'}   依据: {str(res.get('notes') or '')[:80]}")
            check(bool(str(res.get("description") or "").strip()), f"{label} → 有描述")

    # ③b 截图识别：走聊天式路径（助手截图提问的真实路径）
    print("\n③b 截图识别（聊天式，等同助手「截图提问」路径）")
    if shot:
        r = chat_about_image(shot, "image/png",
                             "这是我电脑屏幕的截图。请用中文简要说明：这张图里有什么界面/内容？"
                             "只描述你确实看到的东西，看不清就说看不清。")
        check(r["ok"], "截图 → 模型给出回答", f"{r['elapsed']:.1f}s")
        if r["ok"]:
            print(f"        回答: {r['text'][:200]}")
            results.append((f"屏幕截图（{len(shot) // 1024}KB，聊天式）", r["elapsed"],
                            {"ok": True}))
        else:
            print(f"        error={r.get('error')}")
    else:
        check(False, "截图素材获取")

    # 汇总
    print("\n④ 汇总")
    ok_n = sum(1 for _l, _dt, r in results if r.get("ok"))
    total_s = sum(dt for _l, dt, _r in results)
    print(f"      成功 {ok_n}/{len(results)} 张，总耗时 {total_s:.1f}s，"
          f"平均 {total_s / max(len(results), 1):.1f}s/张")
    print(f"      速率折算：98 张文献图约需 {total_s / max(len(results), 1) * 98 / 60:.1f} 分钟"
          "（本批串行；实际批量按逐张队列执行）")

    if FAILS:
        print(f"\n结果 = FAIL（{len(FAILS)} 项）：" + "；".join(FAILS))
        return 1
    print("\n结果 = ALL PASS（本机视觉模型可用于文献图识别与截图识别）")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        import traceback
        traceback.print_exc()
        print(f"\n结果 = ERROR：{type(exc).__name__}: {exc}")
        sys.exit(2)
