"""阶段二：新旧双版对照验证（Gradio App vs FastAPI + React 链路）。

同一批输入经两条链路（app/gradio_app.py 直调 src/ 后端 vs api/ 封装 src/ 后端）
的结果必须一致。本脚本只读真实 data/，所有写操作（预测日志 / 收藏 / 记录）
均重定向到临时目录。

运行：
    E:\\ANACONDA\\python.exe scripts/phase2_parity_check.py
输出：
    reports/phase2_parity.md
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import time
import traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
for p in (str(PROJECT_ROOT), str(SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

TOL = 0.01  # GNN subprocess 两次调用的允许波动

# ---------------------------------------------------------------------------
# 隔离：任何写数据操作重定向到 tmp（在导入业务模块前 Patch 路径常量）
# ---------------------------------------------------------------------------
TMP = Path(tempfile.mkdtemp(prefix="phase2_parity_"))
REAL_FAV_DIR = PROJECT_ROOT / "data" / "favorites"
REAL_REC_DIR = PROJECT_ROOT / "data" / "rag_export" / "records"
REAL_LOG = PROJECT_ROOT / "data" / "prediction_log.jsonl"


def _snapshot_tree(d: Path) -> dict[str, float]:
    if not d.exists():
        return {}
    return {str(p): p.stat().st_mtime for p in d.rglob("*") if p.is_file()}


SNAP_FAV = _snapshot_tree(REAL_FAV_DIR)
SNAP_REC = _snapshot_tree(REAL_REC_DIR)
SNAP_LOG_MTIME = REAL_LOG.stat().st_mtime if REAL_LOG.exists() else None

import utils.predict_log as predict_log  # noqa: E402
import favorites.store as fav_store  # noqa: E402
import records.store as rec_store  # noqa: E402

predict_log.LOG_PATH = TMP / "prediction_log.jsonl"
fav_store.FAVORITES_DIR = TMP / "favorites"
rec_store.RECORDS_DIR = TMP / "records"

# ---------------------------------------------------------------------------
# 两侧入口导入（gradio 重，仅导入模块不 launch）
# ---------------------------------------------------------------------------
t0 = time.time()
print("[1/4] 导入 Gradio App 与 FastAPI（模型加载，可能较慢）...")
import app.gradio_app as gapp  # noqa: E402
from api import deps  # noqa: E402
from api.main import app as fastapi_app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

PREDICTOR = gapp._get_predictor()  # 单例：两侧共用同一模型实例
deps._PREDICTOR = PREDICTOR
client = TestClient(fastapi_app)
print(f"      导入+模型就绪耗时 {time.time() - t0:.1f}s")

# ---------------------------------------------------------------------------
# 报告收集
# ---------------------------------------------------------------------------
RESULTS: list[dict] = []  # {case, status, detail, numbers}


def add(case: str, status: str, detail: str, numbers: dict | None = None):
    RESULTS.append({"case": case, "status": status,
                    "detail": detail, "numbers": numbers or {}})
    print(f"  [{status}] {case} — {detail}")


# ---------------------------------------------------------------------------
# Gradio 侧：解析 predict() 的 prob_text（HTML/Markdown 混合）
# ---------------------------------------------------------------------------
RE_BIG = re.compile(r'<div class="score-big"[^>]*>\s*([0-9.]+)')
RE_GNN = re.compile(r'\*\*GNN v5\.3\*\*: ([0-9.]+)')
RE_TREE = re.compile(r'\*\*树模型 \((.*?)\)\*\*: ([0-9.]+)')


def gradio_predict(ald: str, amine: str) -> dict:
    """调 gradio_app.predict（页① 对外入口），解析打分文本为结构化数值。"""
    out = gapp.predict(ald, amine)
    prob_text = out[0] or ""
    ood_out = ("模型不适用" in prob_text
               and "均不对该组合输出打分" in prob_text)
    ood_warning = (not ood_out) and "OOD 提示" in prob_text
    big = RE_BIG.search(prob_text)
    gnn = RE_GNN.search(prob_text)
    tree = RE_TREE.search(prob_text)
    return {
        "score": None if ood_out else (float(big.group(1)) if big else None),
        "tree_score": None if ood_out else (float(tree.group(2)) if tree else None),
        "gnn_score": None if ood_out else (float(gnn.group(1)) if gnn else None),
        "ood_level": "out" if ood_out else ("warning" if ood_warning else "none"),
        "raw_head": prob_text[:120].replace("\n", " "),
        "empty_warning": "请先填写" in prob_text,
    }


def api_predict(ald: str, amine: str) -> tuple[int, dict]:
    r = client.post("/api/predict",
                    json={"ald_smiles": ald, "amine_smiles": amine})
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, {}


def close(a, b, tol=TOL) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return abs(a - b) <= tol


def gnn_jitter_probe(ald: str, amine: str, g: dict, a: dict):
    """gradio 侧再预测两次，测 GNN 固有抖动幅度。

    返回 (jitter, benign)：jitter = gradio 三次 GNN 分的最大样本间距；
    若 API 的 GNN 分落在 gradio 样本的抖动包络内（min 跨链差距 ≤ 样本
    间距 + 容差），判为固有波动（benign=True），否则为可疑不一致。
    """
    samples = [g.get("gnn_score")]
    for _ in range(2):
        g2 = gradio_predict(ald, amine)
        if g2.get("gnn_score") is not None:
            samples.append(g2["gnn_score"])
    samples = [s for s in samples if s is not None]
    if len(samples) < 2 or a.get("gnn_score") is None:
        return 0.0, False
    jitter = max(samples) - min(samples)
    cross = min(abs(a["gnn_score"] - s) for s in samples)
    return jitter, cross <= jitter + TOL


# ---------------------------------------------------------------------------
# a. 预测等价用例
# ---------------------------------------------------------------------------
BUILTIN = json.loads(
    (PROJECT_ROOT / "data" / "builtin_monomers.json").read_text(encoding="utf-8"))
BY_NAME = {m["name"]: m for m in BUILTIN}

PREDICT_CASES = [
    # (用例名, 醛, 胺, 期望类别)
    ("P1 内置单体对 A1×对苯二胺",
     "O=Cc1cc(F)c(C=O)cc1F", "Nc1ccc(N)cc1", "score"),
    ("P2 内置单体对 A2×B1",
     BY_NAME["A2"]["smiles"], BY_NAME["B1"]["smiles"], "score"),
    ("P3 内置单体对 TFPB×TAPT",
     BY_NAME["TFPB"]["smiles"], BY_NAME["TAPT"]["smiles"], "score"),
    ("P4 双未见单体对（合法但结构怪异）",
     "O=Cc1c(Cl)cc(Cl)cc1C=O", "Nc1ccc2cc(N)ccc2c1", "any"),
    ("P5 酰肼类胺（应 OOD=out）",
     "O=Cc1cc(F)c(C=O)cc1F", "NNC(=O)c1ccc(C(=O)NN)cc1", "ood_out"),
    ("P6 非法醛 SMILES",
     "O=Cc1cc((", "Nc1ccc(N)cc1", "invalid"),
    ("P7 非法胺 SMILES",
     "O=Cc1cc(F)c(C=O)cc1F", "not_a_smiles", "invalid"),
    ("P8 空输入",
     "", "Nc1ccc(N)cc1", "empty"),
]


def run_predict_cases():
    print("[2/4] 预测等价对照（每组两次预测，GNN subprocess 慢，请耐心等待）...")
    for name, ald, amine, expect in PREDICT_CASES:
        t = time.time()
        try:
            g = gradio_predict(ald, amine)
            code, a = api_predict(ald, amine)
        except Exception as exc:
            add(name, "❌", f"运行异常：{type(exc).__name__}: {exc}")
            continue
        el = time.time() - t
        nums = {"gradio": g, "api_status": code,
                "api": {k: a.get(k) for k in
                        ("score", "tree_score", "gnn_score", "ood")}
                if isinstance(a, dict) else a,
                "elapsed_s": round(el, 1)}

        if expect == "empty":
            ok_g = g["empty_warning"]
            ok_a = code == 400
            add(name, "✅" if (ok_g and ok_a) else "❌",
                f"gradio 空输入警告={ok_g}；API HTTP {code}（期望 400）", nums)
            continue

        if expect == "invalid":
            # 实际行为：predictor 内部优雅降级——两侧均为「不出分 + OOD=out」，
            # API 返回 200 且 score=null（非 4xx/5xx）
            a_ood_inv = (a.get("ood") or {}).get("level") if isinstance(a, dict) else None
            ok_a = code == 200 and a.get("score") is None and a_ood_inv == "out"
            ok_g = g["score"] is None and g["ood_level"] == "out"
            add(name, "✅" if (ok_a and ok_g) else "❌",
                f"两侧均不出分且 OOD=out：gradio={ok_g}（ood={g['ood_level']}）；"
                f"API={ok_a}（HTTP {code}, ood={a_ood_inv}）", nums)
            continue

        if code != 200:
            add(name, "❌", f"API HTTP {code}: {str(a)[:150]}", nums)
            continue

        a_ood = (a.get("ood") or {}).get("level", "none")
        g_ood = g["ood_level"]
        ood_match = a_ood == g_ood
        if expect == "any":
            # 双未见对：OOD 级别两侧一致即可，分数波动走抖动探针
            ok = ood_match
            note = ""
            if a_ood != "out" and not close(g["score"], a.get("score")):
                jitter, ok2 = gnn_jitter_probe(ald, amine, g, a)
                ok = ok and ok2
                note = f"　GNN 抖动探针={jitter}"
            add(name, "✅" if ok else "❌",
                f"OOD gradio={g_ood} api={a_ood}；score "
                f"gradio={g['score']} api={a.get('score')}{note}", nums)
            continue

        if expect == "ood_out":
            ok = (a_ood == "out" and g_ood == "out"
                  and a.get("score") is None and g["score"] is None)
            add(name, "✅" if ok else "❌",
                f"OOD gradio={g_ood} api={a_ood}；score 两侧均为 null："
                f"{a.get('score') is None and g['score'] is None}", nums)
            continue

        # expect == "score"：逐项数值对照
        checks = [
            ("score", close(g["score"], a.get("score"))),
            ("tree_score", close(g["tree_score"], a.get("tree_score"))),
            ("gnn_score", close(g["gnn_score"], a.get("gnn_score"))),
            ("ood", ood_match),
        ]
        bad = [k for k, v in checks if not v]
        detail = (f"score g={g['score']} a={a.get('score')} | "
                  f"tree g={g['tree_score']} a={a.get('tree_score')} | "
                  f"gnn g={g['gnn_score']} a={a.get('gnn_score')} | "
                  f"ood g={g_ood} a={a_ood}")
        status = "✅" if not bad else "❌"
        # 仅 GNN 相关项超容差且树模型逐位一致时，做抖动探针区分
        # 「链路不一致」与「GNN subprocess 固有波动」
        if bad and set(bad) <= {"score", "gnn_score"} and "tree_score" not in bad:
            jitter, benign = gnn_jitter_probe(ald, amine, g, a)
            detail += (f"　GNN 抖动探针：gradio 三次样本极差={jitter:.4f} → "
                       + ("固有波动，非链路问题" if benign else "波动方向不一致"))
            status = "⚠️" if benign else "❌"
        elif bad:
            detail += f"　不一致项: {bad}"
        add(name, status, detail, nums)


# ---------------------------------------------------------------------------
# b. 收藏/记录 CRUD 等价（tmp 隔离）
# ---------------------------------------------------------------------------
def run_crud_cases():
    print("[3/4] 收藏/记录 CRUD 全链路（tmp 隔离）...")
    # C1 收藏创建→读取→删除
    try:
        r = client.post("/api/favorites", json={
            "aldehyde_smiles": "O=Cc1cc(F)c(C=O)cc1F",
            "amine_smiles": "Nc1ccc(N)cc1",
            "ald_name": "A1", "amine_name": "pPDA", "notes": "phase2 对照"})
        assert r.status_code == 201, f"create {r.status_code}: {r.text[:200]}"
        fav = r.json()
        fid = fav["id"]
        r = client.get(f"/api/favorites/{fid}")
        assert r.status_code == 200 and r.json()["id"] == fid
        assert (TMP / "favorites").exists() and list((TMP / "favorites").glob("*.json"))
        assert not any((REAL_FAV_DIR / p).exists()
                       for p in [f"{fid}.json"])
        r = client.delete(f"/api/favorites/{fid}")
        assert r.status_code == 200
        r = client.get(f"/api/favorites/{fid}")
        assert r.status_code == 404
        add("C1 收藏 创建→读取→删除", "✅",
            f"id={fid}；真实 data/favorites 未出现该文件", {"fav_id": fid})
    except Exception as exc:
        add("C1 收藏 创建→读取→删除", "❌", f"{type(exc).__name__}: {exc}")

    # C2 记录创建→读取→删除（挂在收藏上）
    try:
        r = client.post("/api/favorites", json={
            "aldehyde_smiles": "O=Cc1cc(F)c(C=O)cc1F",
            "amine_smiles": "Nc1ccc(N)cc1"})
        fav = r.json()
        fid = fav["id"]
        r = client.post("/api/records", json={
            "favorite_id": fid, "experiment_no": "P2-PARITY-001",
            "aldehyde_smiles": "O=Cc1cc(F)c(C=O)cc1F",
            "amine_smiles": "Nc1ccc(N)cc1",
            "conditions": {"method": "溶剂热"}, "outcome": "film",
            "strength": "中", "notes": "parity", "operator": "phase2"})
        assert r.status_code == 201, f"create rec {r.status_code}: {r.text[:200]}"
        rec = r.json()
        rid = rec["record_id"]
        r = client.get(f"/api/records/{rid}")
        assert r.status_code == 200 and r.json()["record_id"] == rid
        r = client.get("/api/records", params={"favorite_id": fid})
        assert any(x["record_id"] == rid for x in r.json()["records"])
        r = client.delete(f"/api/records/{rid}")
        assert r.status_code == 200
        r = client.get(f"/api/records/{rid}")
        assert r.status_code == 404
        client.delete(f"/api/favorites/{fid}")
        add("C2 记录 创建→读取→删除", "✅",
            f"rec={rid} 挂在 fav={fid} 下，过滤查询命中",
            {"rec_id": rid, "fav_id": fid})
    except Exception as exc:
        add("C2 记录 创建→读取→删除", "❌", f"{type(exc).__name__}: {exc}")

    # C3 真实数据目录零污染校验
    fav_same = SNAP_FAV == _snapshot_tree(REAL_FAV_DIR)
    rec_same = SNAP_REC == _snapshot_tree(REAL_REC_DIR)
    log_same = ((REAL_LOG.stat().st_mtime if REAL_LOG.exists() else None)
                == SNAP_LOG_MTIME)
    ok = fav_same and rec_same and log_same
    add("C3 真实 data/ 零污染", "✅" if ok else "❌",
        f"favorites 未变={fav_same}；records 未变={rec_same}；"
        f"prediction_log.jsonl 未变={log_same}（日志重定向到 "
        f"{predict_log.LOG_PATH}）")


# ---------------------------------------------------------------------------
# c. 方案卡等价
# ---------------------------------------------------------------------------
def run_plan_cases():
    print("[4/4] 方案卡 / 页⑤ 契约对照...")
    ald, amine = "O=Cc1cc(F)c(C=O)cc1F", "Nc1ccc(N)cc1"
    try:
        name_map = gapp.load_builtin_monomers()["name_by_smiles"]
        ald_name = gapp._display_name(ald, name_map)
        amine_name = gapp._display_name(amine, name_map)
        g_card, g_err = gapp._plan_generate(ald, amine, ald_name, amine_name)
        r = client.post("/api/plan-card", json={
            "aldehyde_smiles": ald, "amine_smiles": amine,
            "ald_name": ald_name, "amine_name": amine_name})
        assert r.status_code == 200, f"HTTP {r.status_code}: {r.text[:200]}"
        a_card = r.json()
        assert g_err is None, f"gradio 侧报错: {g_err}"
        # 对照 steps / checklist
        g_steps = g_card.get("steps")
        a_steps = a_card.get("steps")
        g_chk = g_card.get("checklist")
        a_chk = a_card.get("checklist")
        ok = (g_steps == a_steps) and (g_chk == a_chk)
        diff = []
        if g_steps != a_steps:
            diff.append(f"steps 不同（gradio {len(g_steps or [])} 步 vs "
                        f"api {len(a_steps or [])} 步）")
        if g_chk != a_chk:
            diff.append(f"checklist 不同（gradio {len(g_chk or [])} 条 vs "
                        f"api {len(a_chk or [])} 条）")
        add("L1 方案卡 steps/checklist", "✅" if ok else "❌",
            "完全一致" if ok else "；".join(diff),
            {"g_steps_n": len(g_steps or []), "a_steps_n": len(a_steps or []),
             "g_checklist_n": len(g_chk or []), "a_checklist_n": len(a_chk or [])})
    except Exception as exc:
        add("L1 方案卡 steps/checklist", "❌",
            f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-400:]}")

    # d. 页⑤ 契约（只读真实 data/）
    try:
        r = client.get("/api/iterate/suggestions")
        assert r.status_code == 200
        body = r.json()
        sugs = body.get("suggestions")
        assert isinstance(sugs, list), "缺少 suggestions 列表"
        assert isinstance(body.get("count"), int), "缺少 count"
        n = len(sugs)
        # batch 在顶层；confidence/unverified_refs 在 payload 内（契约以
        # sug_20260722_015/016 实测为准）
        with_batch = sum(1 for s in sugs if s.get("batch"))
        with_conf = sum(1 for s in sugs
                        if (s.get("payload") or {}).get("confidence") is not None)
        with_unref = sum(1 for s in sugs
                         if "unverified_refs" in (s.get("payload") or {}))
        if n == 0:
            add("I1 页⑤ suggestions 契约", "⚠️",
                "接口 200 但建议列表为空，无法校验字段")
        else:
            ok = with_batch > 0 and with_conf > 0 and with_unref > 0
            add("I1 页⑤ suggestions 契约", "✅" if ok else "⚠️",
                f"共 {n} 条；含顶层 batch={with_batch} 条、payload.confidence="
                f"{with_conf} 条、payload.unverified_refs={with_unref} 条"
                f"（旧批次建议可能缺新字段）",
                {"total": n, "batch": with_batch,
                 "confidence": with_conf, "unverified_refs": with_unref})
    except Exception as exc:
        add("I1 页⑤ suggestions 契约", "❌", f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------
def write_report():
    lines = [
        "# 阶段二：新旧双版对照验证报告",
        "",
        f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        "- 链路 A（旧版）：`app/gradio_app.py` 直调 `src/` 后端",
        "- 链路 B（新版）：FastAPI `api/`（TestClient 进程内调用）封装同一 `src/` 后端",
        f"- 数值容差：{TOL}（GNN subprocess 两次调用的允许波动）",
        f"- 隔离方式：预测日志 / 收藏 / 记录全部重定向到 `{TMP}`；真实 `data/` 只读",
        "",
        "## 结果总览",
        "",
        "| 用例 | 结果 | 说明 |",
        "|---|---|---|",
    ]
    for r in RESULTS:
        detail = r["detail"].replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {r['case']} | {r['status']} | {detail} |")
    lines += ["", "## 数值对照明细", ""]
    for r in RESULTS:
        if not r["numbers"]:
            continue
        lines.append(f"### {r['case']}")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(r["numbers"], ensure_ascii=False,
                                indent=2, default=str))
        lines.append("```")
        lines.append("")
    n_ok = sum(1 for r in RESULTS if r["status"] == "✅")
    n_warn = sum(1 for r in RESULTS if r["status"] == "⚠️")
    n_bad = sum(1 for r in RESULTS if r["status"] == "❌")
    fixes = [f"- {r['case']}：{r['detail']}" for r in RESULTS
             if r["status"] in ("❌", "⚠️")]
    lines += [
        "## 结论",
        "",
        f"- ✅ {n_ok} 组　⚠️ {n_warn} 组　❌ {n_bad} 组",
        "",
    ]
    if n_bad == 0:
        lines.append("**结论：可切换**" +
                     ("（存在警告项，建议切换前确认）。" if n_warn else "。"))
    else:
        lines.append("**结论：需修复后切换。**")
    if fixes:
        lines += ["", "### 需关注/修复清单", ""] + fixes
    out = PROJECT_ROOT / "reports" / "phase2_parity.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n报告已写入 {out}")
    return n_bad


def main():
    run_predict_cases()
    run_crud_cases()
    run_plan_cases()
    n_bad = write_report()
    print(f"完成：{sum(1 for r in RESULTS if r['status'] == '✅')}/"
          f"{len(RESULTS)} 组通过")
    return 1 if n_bad else 0


if __name__ == "__main__":
    sys.exit(main())
