"""Gradio App 入口：COF 成膜实验指导系统。

输入：醛单体 SMILES + 胺单体 SMILES
输出：成膜概率 + 推荐实验条件 + Word 实验报告
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


def predict(ald_smiles: str, amine_smiles: str) -> tuple[str, str, str]:
    """预测 + 推荐条件的 Gradio 回调函数。"""
    if not ald_smiles.strip() or not amine_smiles.strip():
        return "请输入醛和胺的 SMILES", "", ""

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
    if "tree_probability" in pred_result:
        prob_text += f"- **树模型**: {pred_result['tree_probability']:.3f}\n"
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

    return prob_text, cond_text, "点击生成报告按钮下载"


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
            "输入醛和胺单体的 SMILES，系统将预测成膜概率、推荐实验条件，并生成 Word 实验报告。"
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
                report_output = gr.File(label="实验报告")

        predict_btn.click(
            fn=predict,
            inputs=[ald_input, amine_input],
            outputs=[prob_output, cond_output, gr.Textbox(visible=False)],
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
