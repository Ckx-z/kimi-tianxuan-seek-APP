"""Gradio App 入口：COF 成膜实验指导系统。

输入：醛单体 SMILES + 胺单体 SMILES
输出：成膜概率 + 打分理由（SHAP 归因）+ 推荐实验条件 + Word 实验报告
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import gradio as gr

from condition_recommender import recommend
from predictor import FilmPredictor
from report_generator.exporter import generate_report

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
    """生成「打分理由」：基于 FilmPredictor 实际加载的树模型做 SHAP 归因。

    归因模块懒加载：运行环境缺少 shap 时只降级本板块，不影响预测主流程。
    模型/特征列/特征开关全部从 predictor.tree 动态获取，不硬编码模型路径。
    """
    if not getattr(predictor, "tree_available", False) or getattr(predictor, "tree", None) is None:
        return "### 打分理由（SHAP 归因）\n\n树模型不可用，无法生成打分理由。"
    try:
        from models.attribution import explain_pair_for_app, format_explanation_zh
    except ImportError:
        return ("### 打分理由（SHAP 归因）\n\n"
                "⚠️ 当前 Python 环境缺少 shap 包，打分理由不可用"
                "（在运行 App 的环境中 `pip install shap` 后即可显示）。")
    try:
        tree = predictor.tree
        exp = explain_pair_for_app(
            tree.model,
            tree.feature_cols,
            ald_smiles,
            amine_smiles,
            feature_flags=tree.feature_flags,
        )
        return format_explanation_zh(exp, model_name=tree.model_path.stem)
    except Exception as e:
        return f"### 打分理由（SHAP 归因）\n\n⚠️ 打分理由生成失败（{_brief_error(e)}）"


def predict(ald_smiles: str, amine_smiles: str) -> tuple[str, str, str, str]:
    """预测 + 推荐条件 + 打分理由的 Gradio 回调函数。"""
    if not ald_smiles.strip() or not amine_smiles.strip():
        return "请输入醛和胺的 SMILES", "", "", ""

    predictor = _get_predictor()
    pred_result = predictor.predict(ald_smiles.strip(), amine_smiles.strip())

    # 条件推荐
    conditions = recommend(ald_smiles.strip(), amine_smiles.strip())

    # 格式化概率输出
    prob_text = "### 成膜概率预测\n\n"
    if "gnn_probability" in pred_result:
        prob_text += f"- **GNN v5.3**: {pred_result['gnn_probability']:.3f}"
        if "gnn_std" in pred_result:
            prob_text += f" (±{pred_result['gnn_std']:.3f})"
        prob_text += "\n"
    elif "gnn_error" in pred_result:
        prob_text += f"- **GNN v5.3**: ⚠️ 不可用（{_brief_error(pred_result['gnn_error'])}）\n"
    if "tree_probability" in pred_result:
        tree_name = pred_result.get("tree_model_name", "")
        prob_text += f"- **树模型 ({tree_name})**: {pred_result['tree_probability']:.3f}\n"
    elif "tree_error" in pred_result:
        prob_text += f"- **树模型**: ⚠️ 不可用（{_brief_error(pred_result['tree_error'])}）\n"
    if pred_result.get("ensemble_probability") is not None:
        prob_text += f"- **综合概率**: {pred_result['ensemble_probability']:.3f}\n"

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

    # 打分理由（SHAP 归因，基于实际加载的树模型）
    explain_text = _explain_tree_score(predictor, ald_smiles.strip(), amine_smiles.strip())

    return prob_text, cond_text, "点击生成报告按钮下载", explain_text


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
            "输入醛和胺单体的 SMILES，系统将预测成膜概率、给出打分理由（SHAP 归因）、"
            "推荐实验条件，并生成 Word 实验报告。"
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
                predict_btn = gr.Button("预测成膜概率", variant="primary")
                report_btn = gr.Button("生成 Word 实验报告")

            with gr.Column():
                prob_output = gr.Markdown(label="成膜概率")
                cond_output = gr.Markdown(label="推荐实验条件")
                explain_output = gr.Markdown(label="打分理由")
                report_output = gr.File(label="实验报告")

        predict_btn.click(
            fn=predict,
            inputs=[ald_input, amine_input],
            outputs=[prob_output, cond_output, gr.Textbox(visible=False), explain_output],
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
