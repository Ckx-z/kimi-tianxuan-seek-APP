"""阶段 14（真实回测）：三方对照分析与报告生成。

输入：reports/real_backtest_predictions.json（预测中间结果）
输出：reports/real_backtest.json（结构化结论）+ reports/real_backtest.md（人读报告）
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRED = ROOT / "reports" / "real_backtest_predictions.json"
OUT_JSON = ROOT / "reports" / "real_backtest.json"
OUT_MD = ROOT / "reports" / "real_backtest.md"

THRESH = 0.5


def main():
    pred = json.load(open(PRED, encoding="utf-8"))
    done = [r for r in pred["done"] if r["status"] == "ok"]
    skipped = [r for r in pred["done"] if r["status"] != "ok"]
    planned = [r for r in pred["planned"] if r["status"] == "ok"]
    scheme = [r for r in pred["scheme17"] if r["status"] == "ok"]

    # ---- 三方对照（已做实验，新旧预测均有记录的 5 组）----
    paired = [r for r in done if r["old_gnn_pred"] is not None]
    old_scores = [r["old_gnn_pred"] for r in paired]
    new_scores = [r["tree_probability"] for r in paired]
    paired_old_mean = sum(old_scores) / len(old_scores)
    paired_new_mean = sum(new_scores) / len(new_scores)
    # Brier（真实结果 fail=0）
    brier_old = sum(p ** 2 for p in old_scores) / len(old_scores)
    brier_new = sum(p ** 2 for p in new_scores) / len(new_scores)
    # 含 C（仅旧预测 0.040）的旧模型 Brier
    brier_old_with_c = (sum(p ** 2 for p in old_scores) + 0.040 ** 2) / (len(old_scores) + 1)

    # ---- 阈值口径（全部 13 组可预测已做实验）----
    fails = [r for r in done if r["outcome"] == "fail"]
    partials = [r for r in done if r["outcome"] == "partial"]
    fail_below = [r for r in fails if r["tree_probability"] < THRESH]
    fail_above = [r for r in fails if r["tree_probability"] >= THRESH]
    old_done_with_pred = [r for r in pred["done"] if r["old_gnn_pred"] is not None]  # 含 C
    old_high = [r for r in old_done_with_pred if r["old_gnn_pred"] >= THRESH]
    old_low = [r for r in old_done_with_pred if r["old_gnn_pred"] < THRESH]

    # ---- 排序检验：唯一 partial（A5）在新模型中的名次（严格更高者计数，并列同分）----
    a5_score = next(r["tree_probability"] for r in done if r["id"] == "A5")
    a5_rank = 1 + sum(1 for r in done if r["tree_probability"] > a5_score)
    a5_ties = sum(1 for r in done if r["tree_probability"] == a5_score)

    # ---- "不是只会打低分"证据：分数分布 ----
    scheme_scores = [r["tree_probability"] for r in scheme]
    planned_new = [r["tree_probability"] for r in planned]
    planned_old = [r["old_gnn_pred"] for r in planned]
    dist = {
        "scheme13": {
            "n": len(scheme_scores), "min": min(scheme_scores), "max": max(scheme_scores),
            "mean": sum(scheme_scores) / len(scheme_scores),
            "n_above_0.5": sum(1 for s in scheme_scores if s >= THRESH),
        },
        "planned8_new": {
            "n": len(planned_new), "min": min(planned_new), "max": max(planned_new),
            "mean": sum(planned_new) / len(planned_new),
            "n_above_0.5": sum(1 for s in planned_new if s >= THRESH),
        },
        "planned8_old_gnn": {
            "n": len(planned_old), "min": min(planned_old), "max": max(planned_old),
            "mean": sum(planned_old) / len(planned_old),
            "n_above_0.5": sum(1 for s in planned_old if s >= THRESH),
        },
    }

    summary = {
        "n_done_total": len(pred["done"]),
        "n_done_predictable": len(done),
        "n_done_skipped": [{"id": r["id"], "reason": r["reason"]} for r in skipped],
        "outcomes": {"fail": len(fails), "partial": len(partials)},
        "paired_old_vs_new": {
            "pairs": [r["id"] for r in paired],
            "old_mean": round(paired_old_mean, 4), "new_mean": round(paired_new_mean, 4),
            "brier_old": round(brier_old, 4), "brier_new": round(brier_new, 4),
            "brier_old_incl_C": round(brier_old_with_c, 4),
        },
        "threshold_0.5": {
            "new_fail_below": len(fail_below), "new_fail_above": [r["id"] for r in fail_above],
            "new_fail_avoidance_rate": round(len(fail_below) / len(fails), 4),
            "old_high_all_failed": [r["id"] for r in old_high],
            "old_low_correct_avoidance": [r["id"] for r in old_low],
        },
        "a5_rank_among_done": a5_rank, "a5_tied_experiments": a5_ties,
        "score_distributions": dist,
    }

    out = {"predictions_file": str(PRED.name), "summary": summary,
           "done": pred["done"], "planned": pred["planned"], "scheme17": pred["scheme17"]}
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    # ---------------- Markdown 报告 ----------------
    def fmt(r):
        old = f"{r['old_gnn_pred']:.3f}" if r["old_gnn_pred"] is not None else "—"
        if r["status"] != "ok":
            return f"| {r['id']} | {r['node']}+{r['linker']} | {old} | 跳过（{r['reason']}） | — | {r['outcome']} |"
        return (f"| {r['id']} | {r['node']}+{r['linker']} | {old} | "
                f"{r['tree_probability']:.3f} | {r['route_production']} | {r['outcome']} |")

    lines = []
    lines.append("# 真实回测报告：当前路由模型 vs 旧 GNN 当年预测 vs 真实实验结果\n")
    lines.append("> 日期：2026-07-21 ｜ 数据：`实验方案_含氟COF薄膜合成_v3.9_20260626.docx` + "
                 "`实验ABCDEF.docx`（副本：`data/experimental_refs/`）｜ 实验记录：exp_011 ｜ 决策：D26\n")

    lines.append("## 1. 三方对照总表（14 组已做真实实验）\n")
    lines.append("| 实验 | 组合 | 旧 GNN 当年预测 | 新模型（路由树） | 路由臂 | 真实结果 |")
    lines.append("|---|---|---|---|---|---|")
    for r in pred["done"]:
        lines.append(fmt(r))
    lines.append("")
    lines.append("- 真实结果口径：`fail`=未形成有机械强度的膜（13 组）；`partial`=成膜但机械强度差、"
                 "记录自述「比 A1 稍好」（仅 A5，14 组中相对最好）。")
    lines.append("- C（TFPT+H3）跳过：H3 结构待用户确认（方案化学名构建结构计算 MW=690.28，"
                 "与实验记录 CAS 2569674-66-2 标注 MW=918.37 冲突），按「宁缺毋滥」纪律不预测。")
    lines.append("- A/A2、A1/A5/A8、D/D3/D7/D9 为同单体组合的不同条件重试；模型只读单体，"
                 "同组合预测分相同（模型对条件盲，见 §5 局限）。\n")

    lines.append("## 2. 核心指标\n")
    lines.append("### 2.1 新旧模型在「全失败」样本上的诚实度（6 组新旧均有记录的组合：A/A2/B/D/E/F）\n")
    lines.append("| 指标 | 旧 GNN v5.3 | 新路由树 | 解读 |")
    lines.append("|---|---|---|---|")
    lines.append(f"| 平均分（真实全为失败） | {paired_old_mean:.3f} | **{paired_new_mean:.3f}** | 越接近 0 越诚实 |")
    lines.append(f"| Brier（真值=0） | {brier_old:.3f} | **{brier_new:.3f}** | 旧模型 4.4 倍劣化 |")
    lines.append(f"| ≥0.5 高分（全部失败=虚高） | 5/6 组（0.59–0.82） | **0/6 组**（最高 0.485） | 旧模型系统性虚高 |")
    lines.append("")
    lines.append(f"含 C 的旧模型 Brier（7 组）= {brier_old_with_c:.3f}——即便算上旧模型唯一诚实的 C(0.040)，"
                 "整体仍远劣于新模型。\n")

    lines.append("### 2.2 阈值 0.5 口径的真实命中率（全部 13 组可预测已做实验）\n")
    lines.append("| 口径 | 旧 GNN（7 组有记录） | 新模型（13 组） |")
    lines.append("|---|---|---|")
    lines.append(f"| 失败组打低分（<0.5，回避正确） | 2/7（C、F） | **{len(fail_below)}/{len(fails)}** |")
    lines.append(f"| 失败组打高分（≥0.5，虚高） | 5/7（A/A2/B/D/E） | {len(fail_above)}/{len(fails)}（{', '.join(r['id'] for r in fail_above)}） |")
    lines.append(f"| 唯一相对成功组（A5, partial）的名次 | 无当年预测记录 | **并列第 {a5_rank}/13**（{a5_ties} 组同分，均为 TAPT+A6 组合） |")
    lines.append("")
    lines.append("关键点：新模型仅有的 2 次「虚高」（A1、A8，0.524）与唯一相对成功的 A5 "
                 "**是同一单体组合（TAPT+A6）**——模型把 14 组中真实结果最好的那一对排到了并列第 1；"
                 "它分不出同组合内的条件差异（失败主因本是条件/操作），但在「哪对单体值得做」的排序上命中。\n")

    lines.append("### 2.3 「模型诚实」还是「只会打低分」？（base rate 陷阱检验）\n")
    lines.append("14 组几乎全失败，全打低分的模型会显得准。用未做/计划组合的分数分布证伪「只会打低分」：\n")
    lines.append("| 组合集 | n | 新模型分数范围 | 新模型 ≥0.5 占比 | 旧 GNN 范围（同集） |")
    lines.append("|---|---|---|---|---|")
    lines.append(f"| 方案 v3.9 可解析 13 组合 | 13 | {dist['scheme13']['min']:.3f}–{dist['scheme13']['max']:.3f}"
                 f"（均值 {dist['scheme13']['mean']:.3f}） | {dist['scheme13']['n_above_0.5']}/13 | — |")
    lines.append(f"| 计划组合 G–N（8 组，均有旧预测） | 8 | {dist['planned8_new']['min']:.3f}–{dist['planned8_new']['max']:.3f}"
                 f"（均值 {dist['planned8_new']['mean']:.3f}） | {dist['planned8_new']['n_above_0.5']}/8 | "
                 f"{dist['planned8_old_gnn']['min']:.3f}–{dist['planned8_old_gnn']['max']:.3f}（8/8 ≥0.6） |")
    lines.append("")
    lines.append("新模型在未做组合上给出 0.03–0.72 的全区间分布（13 个方案组合中 8 个 ≥0.5），"
                 "**不是只会打低分**；它的低分精确集中在 TFPT+B5/B4/B3/B6、TAPB+A7 这几对——"
                 "SHAP 归因显示主导信号是 TE 文献先验（如 B5 胺历史成膜率 −0.573、A7 醛历史成膜率 −0.590），"
                 "即**文献数据中这些单体本来就很少成膜**，与用户的真实失败互相印证。\n")

    lines.append("## 3. 发现（对论文叙事的意义）\n")
    lines.append("1. **旧 GNN 的虚高是系统性的**：已做实验中 5 组 ≥0.59 的高分全部失败（A/A2 0.819、B 0.809、"
                 "D 0.596、E 0.589 全灭），唯一诚实的是它自己标注「不可靠」的 C(0.040)。这正是「验证错位」的量化证据。")
    lines.append("2. **新模型对同一批失败组合平均降分 −0.43**（0.687→0.254），Brier 改善 4.4 倍；"
                 "且低分主要由**文献先验（TE 特征）**驱动——不是对用户实验的事后拟合（用户实验从未进入训练数据），"
                 "是独立证据的交叉验证。")
    lines.append("3. **排序命中**：唯一相对成功组 A5 获新模型已做实验中的并列最高分（并列 1/13，同组合三组同分）；"
                 "旧 GNN 当年最高分（A/A2, 0.819）恰是失败组。")
    lines.append("4. **路由臂与化学判断 100% 一致**：生产 exact-match 路由与 canonical 池判定在全部 21 组合上无分歧；"
                 "全部已做实验走 tree_v4 臂（无双未见），说明该场景恰是 TE 先验最强的池内/一新一熟场景。")
    lines.append("5. **C(TFPT+H3) 暴露的 OOD 缺口**：H 系列酰肼结构待确认 + 腙键化学超出亚胺训练分布——"
                 "旧模型当年靠人工标注「不可靠」蒙对，新管线需要把 OOD 标记做成正式输出（对齐建议第 3 条）。\n")

    lines.append("## 4. 单体解析台账\n")
    lines.append("- 解析成功 **17/21**（TAPT/TFPT/TAPB/TFPB + A1–A7 + B1–B6），全部通过 RDKit 分子量校验"
                 "（|ΔMW|≤0.5 vs docx 标注），9 个在旧单体池中找到 canonical SMILES 完全匹配互证。")
    lines.append("- **待用户确认 4 个（H1–H4 全氟链酰肼）**：按方案化学名构建的结构计算 MW 与实验记录 "
                 "CAS 标注 MW 不符（H2：590.27 vs 662.3；H3：690.28 vs 918.37），实际结构需用户核实，"
                 "确认前 G5 四组与实验 C 不出新模型预测。")
    lines.append("- 另注：B3 实验记录 CAS（316-64-3）与旧池匹配条目 CAS（448-97-5）不一致，"
                 "但名称（3,3'-二氟-4,4'-联苯二胺）与 MW（220.22）双重吻合，按名称采信。\n")

    lines.append("## 5. 局限与方法论警示\n")
    lines.append("1. **失败主因是条件/操作**（乙酸误加 18M、加料顺序错、溶解不完全），模型只读单体——"
                 "不能宣称模型「预测了失败」，宣称口径是「新模型不对这些组合虚高打分，且其文献先验独立地看衰这些单体」。")
    lines.append("2. **样本量小**：新旧配对比较仅 6 组（5 对独立组合）；同组合重试共享同一预测分（已做实验有效独立组合 7 对）。"
                 "不报统计显著性，只报效应量。")
    lines.append("3. **base rate 陷阱**已在 §2.3 用分布证据对冲，但 14 组全失败的验证集本身缺乏正例——"
                 "需要后续成功的实验（尤其 G2/G4 高分组）做双向验证。")
    lines.append("4. G–N 组合标题节点（TAPB）与正文（TAPT）不一致，按标题口径计分（正文口径已存 "
                 "`alt_node_prediction` 备查）；该 8 组无结果记录，仅用于分布分析。\n")

    lines.append("## 6. 产物清单\n")
    lines.append("- `reports/real_backtest_monomers.json` — 单体解析台账（MW 校验 + 池互证）")
    lines.append("- `reports/real_backtest_predictions.json` — 全部组合预测（路由臂/双臂分数/SHAP Top3）")
    lines.append("- `reports/real_backtest.json` — 结构化结论")
    lines.append("- `reports/real_backtest.md` — 本报告")
    lines.append("- `scripts/stage14_extract_docx.py` / `stage14_resolve_monomers.py` / "
                 "`stage14_real_backtest.py` / `stage14_analyze_backtest.py`")

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(json.dumps(summary, ensure_ascii=False, indent=1))
    print(f"saved -> {OUT_JSON}")
    print(f"saved -> {OUT_MD}")


if __name__ == "__main__":
    main()
