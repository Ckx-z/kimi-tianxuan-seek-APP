"""P1 冒烟脚本：create_app 构建 + 真实后端联调（一次性，不起服务）。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import gradio_app as g

print("== 1. create_app 构建 ==")
app = g.create_app()
print("OK, theme:", app._deprecated_theme.name if app._deprecated_theme else None)

print("== 2. 内置库加载 ==")
lib = g.load_builtin_monomers()
print("source:", lib["source"], "| 醛", len(lib["aldehydes"]), "胺", len(lib["amines"]))
ald_smiles = dict((v, l) for l, v in lib["aldehydes"])
amine_smiles = dict((v, l) for l, v in lib["amines"])
ald = lib["aldehydes"][0][1]
amine = lib["amines"][0][1]
print("用对:", ald_smiles[ald], "×", amine_smiles[amine])

print("== 3. CAS 离线（内置库路径） ==")
upd_a, upd_b, msg = g.cas_fill("14544-47-9", "胺")  # TAPT
print(msg)

print("== 4. 单组 predict 直调（真实预测器） ==")
out = g.predict(ald, amine)
prob, cond, _, explain, ai, bi, pi, note, similar = out
print(prob[:300])
print("--- SHAP 前 120 字:", explain[:120].replace("\n", " "))
print("--- 结构图:", ai is not None, bi is not None, pi is not None, "| note:", note)
print("--- 相似案例:", similar[:200].replace("\n", " "))

print("== 5. 批量 2 对（内置库多选笛卡尔） ==")
state, table, status = g.batch_predict([ald], [amine, lib["amines"][1][1]], "", None)
print(status)
for row in table:
    print(row)

print("== 6. 导出 CSV ==")
p = g.export_batch_csv(state)
print("导出:", p)

print("== 7. 预测日志 ==")
log = ROOT / "data" / "prediction_log.jsonl"
print("日志存在:", log.exists())
if log.exists():
    lines = log.read_text(encoding="utf-8").strip().splitlines()
    print("最近 2 条:", *[l[:200] for l in lines[-2:]], sep="\n")

print("SMOKE_OK")
