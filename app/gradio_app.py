"""Gradio App 入口：COF 成膜实验指导系统。

输入：醛单体 SMILES + 胺单体 SMILES
输出：成膜概率 + 打分理由（SHAP 归因）+ 推荐实验条件 + Word 实验报告
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import gradio as gr
from rdkit import RDLogger

from condition_recommender import recommend
from predictor import FilmPredictor
from report_generator.exporter import generate_report
from utils.molecule_viz import render_imine_product, smiles_to_image


def _configure_rdkit_logging() -> None:
    """统一 App 进程的 RDKit 日志状态（须在全部导入之后调用）。

    候选池含已知脏 SMILES（特征侧按补 0 优雅降级），每次预测/绘图都会向
    stderr 刷 Parse Error 告警，淹没真正的报错，故默认静默。
    注意导入链中 features.fingerprints 等模块在 import 时也会静默 RDKit，
    因此调试模式必须显式 EnableLog 恢复，而非"不处理"。
    调试入口（start_app.bat）通过设置 COF_RDKIT_DEBUG=1 恢复 RDKit 日志。
    仅影响日志输出，不改变解析与预测行为。
    """
    if os.environ.get("COF_RDKIT_DEBUG"):
        RDLogger.EnableLog("rdApp.*")
        return
    RDLogger.DisableLog("rdApp.*")


_configure_rdkit_logging()

# 全局预测器（懒加载）
_predictor = None


def _get_predictor() -> FilmPredictor:
    global _predictor
    if _predictor is None:
        _predictor = FilmPredictor(use_gnn=True, use_tree=True)
    return _predictor


def _brief_error(err: str, max_len: int = 80) -> str:
    """把可能很长的异常信息截短为一行，便于前端展示。"""
    first_line = str(err).strip().splitlines()[0] if str(err).strip() else "未知错误"
    return first_line[:max_len] + ("…" if len(first_line) > max_len else "")


def _explain_tree_score(predictor: FilmPredictor, ald_smiles: str, amine_smiles: str) -> str:
    """生成「打分理由」：基于该输入实际路由到的树模型做 SHAP 归因。

    归因模块懒加载：运行环境缺少 shap 时只降级本板块，不影响预测主流程。
    双模型路由（D22）下通过 predictor.get_tree_for() 取实际使用的模型：
    池内 tree_v4 走 te_rates 填充路径，外推 tree_v4_noTE 走原 v3 路径；
    单模型模式退回 predictor.tree，不硬编码模型路径。
    """
    if not getattr(predictor, "tree_available", False):
        return "### 打分理由（SHAP 归因）\n\n树模型不可用，无法生成打分理由。"
    try:
        from models.attribution import explain_pair_for_app, format_explanation_zh
    except ImportError:
        return ("### 打分理由（SHAP 归因）\n\n"
                "⚠️ 当前 Python 环境缺少 shap 包，打分理由不可用"
                "（在运行 App 的环境中 `pip install shap` 后即可显示）。")
    try:
        tree, route_info = predictor.get_tree_for(ald_smiles, amine_smiles)
        if tree is None:
            return "### 打分理由（SHAP 归因）\n\n树模型不可用，无法生成打分理由。"
        exp = explain_pair_for_app(
            tree.model,
            tree.feature_cols,
            ald_smiles,
            amine_smiles,
            feature_flags=tree.feature_flags,
            te_rates=tree.te_rates,  # tree_v4 按样本醛/胺填充 TE 先验列；v3/noTE 为 None 无影响
        )
        text = format_explanation_zh(exp, model_name=tree.model_path.stem)
        if route_info:
            text += f"\n\n**模型路由**：{route_info['route_reason']}"
        return text
    except Exception as e:
        return f"### 打分理由（SHAP 归因）\n\n⚠️ 打分理由生成失败（{_brief_error(e)}）"


def _structure_images(ald_smiles: str, amine_smiles: str):
    """渲染醛/胺单体结构图 + 缩合产物骨架图。

    非法 SMILES 优雅降级：对应位置返回 None（前端显示空白），
    并汇总到提示文本，不影响预测主流程。
    """
    ald_img = smiles_to_image(ald_smiles)
    amine_img = smiles_to_image(amine_smiles)
    product_img = render_imine_product(ald_smiles, amine_smiles)

    notes = []
    if ald_img is None:
        notes.append("醛单体 SMILES 无法解析结构")
    if amine_img is None:
        notes.append("胺单体 SMILES 无法解析结构")
    if ald_img is not None and amine_img is not None and product_img is None:
        notes.append("缩合产物骨架图生成失败（不影响其他结果）")
    note_text = "⚠️ " + "；".join(notes) if notes else ""
    return ald_img, amine_img, product_img, note_text


def _format_ood_banner(ood: dict) -> str:
    """OOD 状态横幅：warning 黄条 / out 红条（中文原因）。"""
    if not ood:
        return ""
    level = ood.get("level", "none")
    reasons = "；".join(ood.get("reasons") or [])
    if level == "out":
        return f"> ⛔ **模型不适用**（OOD 检出）：{reasons}\n\n"
    if level == "warning":
        return f"> ⚠️ **OOD 提示**：{reasons}\n\n"
    return ""


def predict(ald_smiles: str, amine_smiles: str):
    """预测 + 结构图 + 推荐条件 + 打分理由的 Gradio 回调函数。"""
    if not ald_smiles.strip() or not amine_smiles.strip():
        return ("请输入醛和胺的 SMILES", "", "", "",
                None, None, None, "")

    predictor = _get_predictor()
    pred_result = predictor.predict(ald_smiles.strip(), amine_smiles.strip())

    # 条件推荐
    conditions = recommend(ald_smiles.strip(), amine_smiles.strip())

    # OOD 状态（三级制，D27）：out → 不显示分数，显示「模型不适用」+ 原因
    ood = pred_result.get("ood") or {}
    ood_out = ood.get("level") == "out"

    # 格式化打分输出（口径：倾向性打分，非严格概率——论文口径 D27）
    prob_text = "### 成膜打分（倾向性）\n\n"
    prob_text += "> 四级软标签上的倾向性打分，非严格概率；对反应条件不敏感。\n\n"
    banner = _format_ood_banner(ood)
    if banner:
        prob_text += banner
    if ood_out:
        # ⛔ 红条：GNN 与树模型同挂 OOD 状态，一律不出分数
        prob_text += ("**GNN 与树模型均不对该组合输出打分**——"
                      "该单体不在模型的化学适用域内，任何数字都不可信。\n")
    else:
        if "gnn_probability" in pred_result:
            prob_text += f"- **GNN v5.3**: {pred_result['gnn_probability']:.3f}"
            if "gnn_std" in pred_result:
                prob_text += f" (±{pred_result['gnn_std']:.3f})"
            prob_text += "\n"
        elif "gnn_error" in pred_result:
            prob_text += f"- **GNN v5.3**: ⚠️ 不可用（{_brief_error(pred_result['gnn_error'])}）\n"
        if "tree_probability" in pred_result:
            tree_name = pred_result.get("tree_model_name", "")
            prob_text += f"- **树模型 ({tree_name})**: {pred_result['tree_probability']:.3f}"
            if pred_result.get("score_std"):
                prob_text += f" (±{pred_result['score_std']:.3f})"
            prob_text += "\n"
            if pred_result.get("tree_route_reason"):
                prob_text += f"  - 模型路由：{pred_result['tree_route_reason']}\n"
        elif "tree_error" in pred_result:
            prob_text += f"- **树模型**: ⚠️ 不可用（{_brief_error(pred_result['tree_error'])}）\n"
        if pred_result.get("ensemble_probability") is not None:
            prob_text += f"- **综合打分**: {pred_result['ensemble_probability']:.3f}"
            if pred_result.get("score_std"):
                prob_text += f" (±{pred_result['score_std']:.3f})"
            prob_text += "\n"

    # 格式化条件输出
    cond_text = "### 推荐实验条件\n\n"
    cond_text += f"- **合成方法**: {conditions.get('method', 'N/A')}\n"
    cond_text += f"- **溶剂体系**: {conditions.get('solvent_system', 'N/A')}\n"
    cond_text += f"- **溶剂比例**: {conditions.get('solvent_ratio', 'N/A')}\n"
    cond_text += f"- **反应温度**: {conditions.get('temperature', 'N/A')}\n"
    cond_text += f"- **反应时间**: {conditions.get('time', 'N/A')}\n"
    cond_text += f"- **催化剂**: {conditions.get('catalyst', 'N/A')}\n"
    cond_text += f"- **当量比**: {conditions.get('stoichiometry', 'N/A')}\n"
    cond_text += f"- **备注**: {conditions.get('notes', 'N/A')}\n\n"
    cond_text += f"**相似历史案例**: {conditions.get('case_description', 'N/A')} "
    cond_text += f"(相似度 {conditions.get('case_similarity_score', 0):.2f})\n"

    # 打分理由（SHAP 归因，基于实际加载的树模型）；ood=out 时不显示理由
    if ood_out:
        explain_text = ("### 打分理由（SHAP 归因）\n\n"
                        "⛔ OOD 检出（模型不适用），不提供打分理由——"
                        "对不适用样本解释一个不存在的分数没有意义。")
    else:
        explain_text = _explain_tree_score(predictor, ald_smiles.strip(), amine_smiles.strip())

    # 单体结构图 + 缩合产物骨架图（解析失败优雅降级）
    ald_img, amine_img, product_img, struct_note = _structure_images(
        ald_smiles.strip(), amine_smiles.strip()
    )

    return (prob_text, cond_text, "点击生成报告按钮下载", explain_text,
            ald_img, amine_img, product_img, struct_note)


def generate_report_callback(ald_smiles: str, amine_smiles: str) -> str:
    """生成报告并返回路径。"""
    if not ald_smiles.strip() or not amine_smiles.strip():
        return ""

    predictor = _get_predictor()
    pred_result = predictor.predict(ald_smiles.strip(), amine_smiles.strip())
    conditions = recommend(ald_smiles.strip(), amine_smiles.strip())

    report_path = generate_report(
        ald_smiles.strip(),
        amine_smiles.strip(),
        pred_result,
        conditions,
    )
    return str(report_path)


def create_app() -> gr.Blocks:
    """创建 Gradio App。"""
    with gr.Blocks(title="COF 成膜实验指导系统") as app:
        gr.Markdown("# COF 成膜实验指导系统")
        gr.Markdown(
            "输入醛和胺单体的 SMILES，系统将给出成膜打分（倾向性）、打分理由（SHAP 归因）、"
            "推荐实验条件，并生成 Word 实验报告。"
            "打分附带 ± 认知不确定度；检出 OOD（非标准官能团 / 双未见 / 特征超分布）时"
            "会以 ⚠️/⛔ 提示，⛔ 时不输出分数。"
        )

        with gr.Row():
            with gr.Column():
                ald_input = gr.Textbox(
                    label="醛单体 SMILES",
                    placeholder="例如：O=CC1=C(C=O)C(=O)C(C=O)=C1O",
                    value="O=CC1=C(C=O)C(=O)C(C=O)=C1O",
                )
                amine_input = gr.Textbox(
                    label="胺单体 SMILES",
                    placeholder="例如：Nc1ccc(N)cc1",
                    value="Nc1ccc(N)cc1",
                )
                predict_btn = gr.Button("预测成膜打分", variant="primary")
                report_btn = gr.Button("生成 Word 实验报告")

            with gr.Column():
                prob_output = gr.Markdown(label="成膜打分（倾向性）")
                cond_output = gr.Markdown(label="推荐实验条件")
                explain_output = gr.Markdown(label="打分理由")
                report_output = gr.File(label="实验报告")

        gr.Markdown("### 化学结构")
        with gr.Row():
            ald_img_output = gr.Image(label="醛单体", height=280)
            amine_img_output = gr.Image(label="胺单体", height=280)
            product_img_output = gr.Image(label="缩合产物骨架（亚胺键 C=N 示意）", height=280)
        struct_note_output = gr.Markdown()

        predict_btn.click(
            fn=predict,
            inputs=[ald_input, amine_input],
            outputs=[prob_output, cond_output, gr.Textbox(visible=False), explain_output,
                     ald_img_output, amine_img_output, product_img_output, struct_note_output],
        )

        report_btn.click(
            fn=generate_report_callback,
            inputs=[ald_input, amine_input],
            outputs=[report_output],
        )

    return app


if __name__ == "__main__":
    app = create_app()
    app.launch(server_name="127.0.0.1", server_port=7860, share=False)
