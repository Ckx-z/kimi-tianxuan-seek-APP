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

print("== 8. P2 收藏全流程（真实后端，收尾清理） ==")
g._LAST_PREDICTION.clear()
g._LAST_PREDICTION.update({"ald": ald, "amine": amine, "pred": {"tree_probability": 0.5}})
msg = g.favorite_current(ald, amine, "冒烟测试")
print(msg)
fid = msg.split("（")[1].split("）")[0] if "（fav_" in msg else None
assert fid, "应返回收藏 id"

print("== 8b. 主分数口径（主分=两模型较高值 max(树,GNN)） ==")
import re as _re
headline = prob.split("### 成膜打分")[0]
m_main = _re.search(r'score-big[^>]*>([\d.]+)', headline)
assert m_main, "主分数应存在"
assert "两模型较高值" in headline, "主分数旁须标注「两模型较高值」"
m_tree = _re.search(r'\*\*树模型 \([^)]+\)\*\*: ([\d.]+)', prob)
m_gnn = _re.search(r'\*\*GNN v5\.3\*\*: ([\d.]+)', prob)
_vals = [float(m.group(1)) for m in (m_tree, m_gnn) if m]
assert _vals, "树/GNN 至少一方出分"
_expected = f"{max(_vals):.3f}"
assert m_main.group(1) == _expected, f"主分数必须 == max(树,GNN) = {_expected}"
print("主分数", m_main.group(1), "== max(树",
      m_tree.group(1) if m_tree else "未出分",
      ", GNN", m_gnn.group(1) if m_gnn else "未出分", ")")
assert "主分数取两模型较高者，属乐观召回口径" in prob, "来源说明须存在"
assert "综合打分（树与 GNN 平均，仅对照参考）" in prob, "综合分须明确标注"

print("== 8c. 收藏去重提示（同 SMILES 对不新建） ==")
msg_dup = g.favorite_current(ald, amine, "")
print(msg_dup)
assert "已收藏过" in msg_dup and fid in msg_dup, "重复收藏应提示已收藏过"

cards, sel, status = g.refresh_favorites()
print("卡片墙:", status, "| 徽章:", "fav-badge" in cards, "| 结构图:", "base64" in cards)
assert any(c[1] == fid for c in sel["choices"]), "下拉应含新收藏"
info, snap, notes, refs, recs = g.show_favorite_detail(fid)
print("详情:", info.splitlines()[0], "| 快照:", snap[:50])
assert fid in info and "醛 SMILES" in info, "选中后详情应展示完整信息"
assert "最新预测快照" in snap
print("文献自动匹配条数:", refs.count("相关文献·自动匹配"))
plan_html, st = g.plan_card_for_favorite(fid)
print("方案卡:", st, "| 防错清单:", "防错清单" in plan_html)
st, timeline, _ = g.submit_record(fid, "甲苯", "", "6M 乙酸", "120", "3",
                                  "先醛后胺", "部分成膜", "", "冒烟", "测试")
print(st, "| 时间线含对比:", "实际" in timeline)

print("== 9. 游离实验记录（不关联收藏） ==")
st2, timeline2, _ = g.submit_record("", "甲苯", "", "6M 乙酸", "120", "3",
                                    "", "成膜", "", "冒烟", "游离测试",
                                    True, ald, amine)
print(st2)
assert "✓" in st2, "游离记录应保存成功（后端签名已就位）"
assert "游离" not in timeline2 or "×" in timeline2

print("== 10. 页⑤ 方案迭代展示 ==")
summary5, timeline5, sug_html5, sel5, status5 = g.refresh_iteration_tab("")
print("摘要:", summary5[:80])
print("建议区:", (status5 or sug_html5[:80]))
assert summary5 and sug_html5
html_sug2, status_sug2 = g.refresh_suggestions(fid)
print("按收藏过滤:", status_sug2 or html_sug2[:60])

# 清理冒烟数据
import json as _json
rec_dir = ROOT / "data" / "rag_export" / "records"
for p in rec_dir.glob("rec_*.json"):
    if p.name != "example.json" and "冒烟" in p.read_text(encoding="utf-8"):
        p.unlink()
st, *_ = g.delete_favorite(fid)
print(st, "（冒烟数据已清理）")

print("SMOKE_OK")
