"""Gradio App 入口：COF 成膜单体推荐系统（P1 改版）。

五标签页架构（docs/APP_REDESIGN_PROPOSAL.md 第 2/3/5 节）：
- ① 查询打分：SMILES 直输 / CAS 号解析 / 内置单体库点选，输出打分±std、路由臂、
  GNN 对照、OOD ⛔/⚠️、SHAP 中文理由、结构图三件套、相似成膜案例、Word 报告。
- ② 批量排序：内置库多选（醛×胺笛卡尔组合）/ 粘贴 SMILES 对 / 上传 CSV →
  排序表（打分±std、路由臂、OOD、Top 理由一句话）→ 排序/过滤 → 导出 CSV。
- ③④⑤：占位（P2/P3 上线）。

依赖任务2的三个后端模块（src/utils/cas_lookup.py、src/utils/predict_log.py、
src/recommend/similar_cases.py），均为懒加载 + 优雅降级：模块未就位时
对应板块显示提示而不报错。
"""

from __future__ import annotations

import csv
import json
import os
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import gradio as gr
from rdkit import RDLogger

from condition_recommender import recommend
from predictor import FilmPredictor
from report_generator.exporter import generate_report
from utils.molecule_viz import render_imine_product, smiles_to_image

MODEL_VERSION_BADGE = "tree_v4 路由 · GNN v5.3"
MAX_BATCH_PAIRS = 20
BUILTIN_MONOMERS_PATH = PROJECT_ROOT / "data" / "builtin_monomers.json"
BACKTEST_MONOMERS_PATH = PROJECT_ROOT / "reports" / "real_backtest_monomers.json"
BATCH_EXPORT_DIR = PROJECT_ROOT / "reports" / "exports"


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


# ---------------------------------------------------------------------------
# 任务2 后端模块的安全封装（懒加载；未就位时优雅降级，绝不让 UI 崩）
# ---------------------------------------------------------------------------

def _resolve_cas(cas: str) -> tuple[dict | None, str | None]:
    """CAS → SMILES。返回 (info, 错误提示)；info 为 {smiles, name, source}。

    - 模块未上线 → (None, 模块提示)
    - 解析失败 → (None, 人话错误)，不静默猜
    """
    cas = (cas or "").strip()
    if not cas:
        return None, "请输入 CAS 号（例如 14544-47-9）。"
    try:
        from utils.cas_lookup import resolve_cas
    except ImportError:
        return None, "CAS 查询模块尚未上线（后端开发中），请先用 SMILES 直输或内置库点选。"
    try:
        info = resolve_cas(cas)
    except Exception as e:
        return None, f"CAS 解析出错：{_brief_error(e)}"
    if not info or not info.get("smiles"):
        return None, (f"未找到 CAS {cas} 对应的化合物——"
                      "离线时仅内置单体可用；在线查询 PubChem 需要网络。")
    return info, None


def _find_similar_cases(ald_smiles: str, amine_smiles: str, top_k: int = 3) -> tuple[list | None, str | None]:
    """相似成膜案例。返回 (cases, 提示文案)；模块未上线时 cases 为 None。"""
    try:
        from recommend.similar_cases import find_similar_film_cases
    except ImportError:
        return None, "⏳ 相似成膜案例模块即将上线（后端开发中）。"
    try:
        cases = find_similar_film_cases(ald_smiles, amine_smiles, top_k=top_k)
        return cases or [], None
    except Exception as e:
        return None, f"⚠️ 相似案例查询失败（{_brief_error(e)}），不影响其他结果。"


def _log_prediction(record: dict) -> bool:
    """预测日志落盘。日志模块缺失或写盘失败都不影响主流程，返回是否成功。"""
    try:
        from utils.predict_log import log_prediction
    except ImportError:
        return False
    try:
        log_prediction(record)
        return True
    except Exception:
        return False


def _build_log_record(ald_smiles: str, amine_smiles: str, pred_result: dict,
                      source: str) -> dict:
    """按数据契约（方案第 6 节）组装 prediction 日志记录。"""
    ood = pred_result.get("ood") or {}
    return {
        "schema_version": "1.0",
        "type": "prediction",
        "ald_smiles": ald_smiles,
        "amine_smiles": amine_smiles,
        "score": pred_result.get("tree_probability", pred_result.get("ensemble_probability")),
        "std": pred_result.get("score_std"),
        "arm": pred_result.get("tree_model_name"),
        "route": pred_result.get("tree_route"),
        "ood_level": ood.get("level", "none"),
        "model_version": "tree_v4_routed+gnn_v5.3",
        "source": source,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }


# ---------------------------------------------------------------------------
# 内置单体库
# ---------------------------------------------------------------------------

_MONOMER_CACHE: dict | None = None


def load_builtin_monomers() -> dict:
    """加载内置单体库，按角色分醛/胺两组。

    优先读 data/builtin_monomers.json（任务2 维护）；缺席时回退
    reports/real_backtest_monomers.json（17 个已解析内置单体），保证离线可用。

    返回 {"aldehydes": [(label, smiles)], "amines": [(label, smiles)],
          "name_by_smiles": {smiles: 显示名}, "source": 来源说明}
    """
    global _MONOMER_CACHE
    if _MONOMER_CACHE is not None:
        return _MONOMER_CACHE

    entries: list[dict] = []
    source = ""
    if BUILTIN_MONOMERS_PATH.exists():
        try:
            raw = json.loads(BUILTIN_MONOMERS_PATH.read_text(encoding="utf-8"))
            pool = raw.get("monomers", raw) if isinstance(raw, dict) else raw
            items = pool.values() if isinstance(pool, dict) else pool
            for it in items:
                smiles = it.get("smiles") or it.get("canonical_smiles")
                if not smiles:
                    continue
                entries.append({
                    "id": it.get("id") or it.get("name") or "?",
                    "name": (it.get("short_desc") or it.get("docx_name")
                             or it.get("name") or it.get("id") or "?"),
                    "cas": it.get("cas") or it.get("cas_in_record"),
                    "role": str(it.get("role") or it.get("type") or "").lower(),
                    "smiles": smiles,
                })
            source = "data/builtin_monomers.json"
        except Exception:
            entries = []

    if not entries and BACKTEST_MONOMERS_PATH.exists():
        raw = json.loads(BACKTEST_MONOMERS_PATH.read_text(encoding="utf-8"))
        for it in raw.get("monomers", {}).values():
            if it.get("status") != "resolved":
                continue
            entries.append({
                "id": it.get("id", "?"),
                "name": it.get("docx_name") or it.get("id", "?"),
                "cas": it.get("cas_in_record"),
                "role": str(it.get("type", "")).lower(),
                "smiles": it.get("canonical_smiles", ""),
            })
        source = "reports/real_backtest_monomers.json（回退）"

    aldehydes, amines, name_by_smiles = [], [], {}
    for e in entries:
        if not e["smiles"]:
            continue
        cas_part = f" · CAS {e['cas']}" if e.get("cas") else ""
        label = f"{e['id']}｜{e['name']}{cas_part}"
        name_by_smiles[e["smiles"]] = f"{e['id']} {e['name']}"
        if "aldehyde" in e["role"]:
            aldehydes.append((label, e["smiles"]))
        elif "amine" in e["role"]:
            amines.append((label, e["smiles"]))
        # hydrazide 等角色不进醛/胺库（腙键体系模型不适用）

    _MONOMER_CACHE = {
        "aldehydes": sorted(aldehydes),
        "amines": sorted(amines),
        "name_by_smiles": name_by_smiles,
        "source": source or "（无可用内置库）",
    }
    return _MONOMER_CACHE


def _display_name(smiles: str, name_by_smiles: dict) -> str:
    """批量表里的人类可读单体名：命中内置库用库名，否则截断 SMILES。"""
    if smiles in name_by_smiles:
        return name_by_smiles[smiles]
    return smiles if len(smiles) <= 18 else smiles[:17] + "…"


# ---------------------------------------------------------------------------
# 展示辅助
# ---------------------------------------------------------------------------

def _brief_error(err: str, max_len: int = 80) -> str:
    """把可能很长的异常信息截短为一行，便于前端展示。"""
    first_line = str(err).strip().splitlines()[0] if str(err).strip() else "未知错误"
    return first_line[:max_len] + ("…" if len(first_line) > max_len else "")


def _score_color(score: float) -> str:
    """打分颜色梯度：深青（高）/ 琥珀（中）/ 红（低）。"""
    if score >= 0.6:
        return "#0f766e"
    if score >= 0.4:
        return "#b45309"
    return "#b91c1c"


def _big_score_html(score: float, std: float | None) -> str:
    std_part = f'<span class="score-std"> ± {std:.3f}</span>' if std else ""
    return (f'<div class="score-big" style="color:{_score_color(score)}">'
            f"{score:.3f}{std_part}</div>")


def _ood_label(level: str) -> str:
    return {"none": "✓ 池内", "warning": "⚠️ 外推", "out": "⛔ 不适用"}.get(level, level)


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


def _format_similar_cases(cases: list | None, status_msg: str | None) -> str:
    """相似成膜案例板块：配对、结果、文献 paper_id、相似度。

    兼容任务2 的返回 schema：{"aldehyde_smiles", "amine_smiles", "is_film",
    "paper_id", "similarity"}；SMILES 命中内置库时显示中文名。
    """
    head = "### 相似成膜案例\n\n"
    if status_msg:
        return head + status_msg
    if not cases:
        return head + "训练文献中未找到与该醛/胺相近的成膜案例。"
    name_map = load_builtin_monomers()["name_by_smiles"]
    lines = [head, "训练文献中与该醛/胺最接近的成功配对：", ""]
    for i, c in enumerate(cases, 1):
        ald = c.get("aldehyde") or _display_name(c.get("aldehyde_smiles", ""), name_map)
        amine = c.get("amine") or _display_name(c.get("amine_smiles", ""), name_map)
        pair_desc = c.get("description") or (
            f"{ald} + {amine}" if (ald or amine) else "—")
        score = c.get("score", c.get("film_score"))
        if isinstance(score, (int, float)):
            result_txt = f"打分 {score:.3f}"
        elif c.get("is_film") is not None:
            result_txt = "成膜成功" if c["is_film"] else "未成膜"
        else:
            result_txt = "—"
        pid = c.get("paper_id", "?")
        sim = c.get("similarity")
        sim_txt = f"{sim:.2f}" if isinstance(sim, (int, float)) else "—"
        lines.append(f"{i}. **{pair_desc}** — {result_txt} · "
                     f"文献 {pid} · 相似度 {sim_txt}")
    return "\n".join(lines)


def _explain_tree_score(predictor: FilmPredictor, ald_smiles: str, amine_smiles: str) -> str:
    """生成「打分理由」：基于该输入实际路由到的树模型做 SHAP 归因。"""
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
            te_rates=tree.te_rates,
        )
        text = format_explanation_zh(exp, model_name=tree.model_path.stem)
        if route_info:
            text += f"\n\n**模型路由**：{route_info['route_reason']}"
        return text
    except Exception as e:
        return f"### 打分理由（SHAP 归因）\n\n⚠️ 打分理由生成失败（{_brief_error(e)}）"


def _one_line_reason(predictor: FilmPredictor, ald_smiles: str, amine_smiles: str) -> str:
    """批量表用的「Top 理由一句话」：SHAP 贡献最大的特征 + 推/拉方向。"""
    try:
        from models.attribution import explain_pair_for_app
        tree, _ = predictor.get_tree_for(ald_smiles, amine_smiles)
        if tree is None:
            return "—"
        exp = explain_pair_for_app(
            tree.model, tree.feature_cols, ald_smiles, amine_smiles,
            feature_flags=tree.feature_flags, te_rates=tree.te_rates, top_k=1,
        )
        pick = None
        for recs in (exp.get("top_positive_features"), exp.get("top_negative_features")):
            if recs:
                pick = recs[0]
                break
        if not pick:
            return "—"
        direction = "推高" if pick["shap"] > 0 else "拉低"
        return f"{pick.get('label_zh') or pick['feature']} {direction}打分"
    except Exception:
        return "—"


def _structure_images(ald_smiles: str, amine_smiles: str):
    """渲染醛/胺单体结构图 + 缩合产物骨架图（非法 SMILES 优雅降级）。"""
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


# ---------------------------------------------------------------------------
# 页① 查询打分：回调
# ---------------------------------------------------------------------------

_EMPTY_HINT = "*尚无结果——请在左侧输入 SMILES、解析 CAS 或从内置库点选单体，然后点击「开始打分」。*"


def predict(ald_smiles: str, amine_smiles: str):
    """单组预测回调：打分 + 条件 + SHAP + 结构图 + 相似案例，并写预测日志。"""
    if not ald_smiles.strip() or not amine_smiles.strip():
        return ("⚠️ 请先填写醛和胺的 SMILES（可用 CAS 解析或内置库点选自动填入）。",
                "", "", "", None, None, None, "", "")

    ald_smiles = ald_smiles.strip()
    amine_smiles = amine_smiles.strip()
    predictor = _get_predictor()
    pred_result = predictor.predict(ald_smiles, amine_smiles)

    # 预测日志（D23 路由复盘 / 使用统计）
    _log_prediction(_build_log_record(ald_smiles, amine_smiles, pred_result, "single"))

    # 条件推荐
    conditions = recommend(ald_smiles, amine_smiles)

    # OOD 状态（三级制，D27）：out → 不显示分数，显示「模型不适用」+ 原因
    ood = pred_result.get("ood") or {}
    ood_out = ood.get("level") == "out"

    # 格式化打分输出（口径：倾向性打分，非严格概率——论文口径 D27）
    prob_text = ""
    if not ood_out:
        main_score = pred_result.get("tree_probability",
                                     pred_result.get("ensemble_probability"))
        if main_score is not None:
            prob_text += _big_score_html(main_score, pred_result.get("score_std")) + "\n\n"
    prob_text += "### 成膜打分（倾向性）\n\n"
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

    # 打分理由（SHAP 归因）；ood=out 时不显示理由
    if ood_out:
        explain_text = ("### 打分理由（SHAP 归因）\n\n"
                        "⛔ OOD 检出（模型不适用），不提供打分理由——"
                        "对不适用样本解释一个不存在的分数没有意义。")
    else:
        explain_text = _explain_tree_score(predictor, ald_smiles, amine_smiles)

    # 相似成膜案例（任务2 后端；未上线时显示占位提示）
    cases, cases_msg = _find_similar_cases(ald_smiles, amine_smiles, top_k=3)
    similar_text = _format_similar_cases(cases, cases_msg)

    # 单体结构图 + 缩合产物骨架图（解析失败优雅降级）
    ald_img, amine_img, product_img, struct_note = _structure_images(ald_smiles, amine_smiles)

    return (prob_text, cond_text, "点击「生成 Word 实验报告」按钮下载", explain_text,
            ald_img, amine_img, product_img, struct_note, similar_text)


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


def cas_fill(cas: str, role: str):
    """CAS 解析回调：成功填入对应 SMILES 框，失败给人话报错，不静默。"""
    info, err = _resolve_cas(cas)
    if err:
        return gr.update(), gr.update(), f"⚠️ {err}"
    name = info.get("name") or ""
    src = info.get("source") or "未知来源"
    msg = f"✓ 已解析 CAS {cas.strip()}：{name}（来源：{src}），已填入「{role}」SMILES。"
    if role == "醛":
        return gr.update(value=info["smiles"]), gr.update(), msg
    return gr.update(), gr.update(value=info["smiles"]), msg


# ---------------------------------------------------------------------------
# 页② 批量排序：回调
# ---------------------------------------------------------------------------

BATCH_HEADERS = ["醛", "胺", "成膜打分（倾向性）", "±std", "路由臂", "OOD", "Top 理由"]


def _parse_pairs(ald_choices, amine_choices, pasted_text, csv_file) -> tuple[list, list]:
    """汇总三种输入来源为去重后的 (醛, 胺) 列表。返回 (pairs, notes)。"""
    pairs: list[tuple[str, str]] = []
    notes: list[str] = []

    # 1) 内置库多选：醛 × 胺 笛卡尔组合（choices 的 value 即 SMILES）
    ald_sel = [s for s in (ald_choices or []) if s]
    amine_sel = [s for s in (amine_choices or []) if s]
    if ald_sel and amine_sel:
        for a in ald_sel:
            for b in amine_sel:
                pairs.append((a, b))
        notes.append(f"内置库组合：{len(ald_sel)} 醛 × {len(amine_sel)} 胺 "
                     f"= {len(ald_sel) * len(amine_sel)} 对")
    elif ald_sel or amine_sel:
        notes.append("⚠️ 内置库多选需同时选择醛和胺才能两两组合，已忽略单边选择。")

    # 2) 粘贴文本：每行一对（逗号 / 制表符 / 空白分隔）
    for line in (pasted_text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        for sep in (",", "，", "\t"):
            if sep in line:
                parts = [p.strip() for p in line.split(sep)]
                break
        else:
            parts = line.split()
        if len(parts) >= 2 and parts[0] and parts[1]:
            pairs.append((parts[0], parts[1]))
        else:
            notes.append(f"⚠️ 跳过无法解析的行：「{line[:40]}」（应为每行一对：醛SMILES, 胺SMILES）")

    # 3) CSV 上传：前两列
    if csv_file:
        path = csv_file.name if hasattr(csv_file, "name") else str(csv_file)
        try:
            with open(path, newline="", encoding="utf-8-sig") as f:
                reader = csv.reader(f)
                rows = [r for r in reader if len(r) >= 2 and r[0].strip() and r[1].strip()]
            # 首行若是表头（含非 SMILES 字样）则跳过
            if rows and any(k in rows[0][0].lower() for k in ("ald", "醛", "smiles")):
                rows = rows[1:]
            for r in rows:
                pairs.append((r[0].strip(), r[1].strip()))
            notes.append(f"CSV：读取 {len(rows)} 对")
        except Exception as e:
            notes.append(f"⚠️ CSV 读取失败（{_brief_error(e)}），已忽略该文件。")

    # 去重保序
    seen, unique = set(), []
    for p in pairs:
        if p not in seen:
            seen.add(p)
            unique.append(p)

    if len(unique) > MAX_BATCH_PAIRS:
        notes.append(f"⚠️ 共 {len(unique)} 对，超出单次上限 {MAX_BATCH_PAIRS} 对，"
                     f"已截断为前 {MAX_BATCH_PAIRS} 对。")
        unique = unique[:MAX_BATCH_PAIRS]
    return unique, notes


def batch_predict(ald_choices, amine_choices, pasted_text, csv_file):
    """批量预测回调：逐对调 FilmPredictor，结果存入 gr.State 并渲染排序表。"""
    pairs, notes = _parse_pairs(ald_choices, amine_choices, pasted_text, csv_file)
    if not pairs:
        status = "⚠️ 没有可预测的单体对——请用内置库多选（醛+胺）、粘贴 SMILES 对或上传 CSV。"
        if notes:
            status += "\n\n" + "\n".join(notes)
        return {"rows": []}, [], status

    lib = load_builtin_monomers()
    name_map = lib["name_by_smiles"]
    predictor = _get_predictor()

    rows = []
    for ald, amine in pairs:
        try:
            pred = predictor.predict(ald, amine)
        except Exception as e:
            rows.append({
                "ald": ald, "amine": amine,
                "ald_name": _display_name(ald, name_map),
                "amine_name": _display_name(amine, name_map),
                "score": None, "std": None, "arm": "—",
                "ood_level": "error", "reason": f"预测失败：{_brief_error(e, 40)}",
            })
            continue

        _log_prediction(_build_log_record(ald, amine, pred, "batch"))
        ood = pred.get("ood") or {}
        level = ood.get("level", "none")
        if level == "out":
            score, std, reason = None, None, "OOD 不适用，不出分"
        else:
            score = pred.get("tree_probability", pred.get("ensemble_probability"))
            std = pred.get("score_std")
            reason = _one_line_reason(predictor, ald, amine)
        rows.append({
            "ald": ald, "amine": amine,
            "ald_name": _display_name(ald, name_map),
            "amine_name": _display_name(amine, name_map),
            "score": score, "std": std,
            "arm": pred.get("tree_model_name", "—"),
            "ood_level": level, "reason": reason,
        })

    state = {"rows": rows}
    n_ok = sum(1 for r in rows if r["score"] is not None)
    status = f"✓ 完成 {len(rows)} 对预测（{n_ok} 对出分，{len(rows) - n_ok} 对不适用/失败）。"
    if notes:
        status += "\n\n" + "\n".join(notes)
    return state, _render_batch_rows(rows, "按打分降序", "全部"), status


def _render_batch_rows(rows: list, sort_by: str, ood_filter: str) -> list:
    """按排序/过滤条件把 state 行渲染为 Dataframe 数据。"""
    visible = list(rows)
    if ood_filter == "隐藏 ⛔ 不适用":
        visible = [r for r in visible if r["ood_level"] != "out"]
    elif ood_filter == "仅看 ✓ 池内":
        visible = [r for r in visible if r["ood_level"] == "none"]

    if sort_by == "按打分降序":
        visible.sort(key=lambda r: r["score"] if r["score"] is not None else -1.0,
                     reverse=True)
    elif sort_by == "按打分升序":
        visible.sort(key=lambda r: r["score"] if r["score"] is not None else float("inf"))
    elif sort_by == "按不确定度降序":
        visible.sort(key=lambda r: r["std"] if r["std"] is not None else -1.0,
                     reverse=True)

    return [[
        r["ald_name"], r["amine_name"],
        round(r["score"], 3) if r["score"] is not None else "⛔",
        f"±{r['std']:.3f}" if r["std"] is not None else "—",
        r["arm"], _ood_label(r["ood_level"]) if r["ood_level"] != "error" else "⚠️ 失败",
        r["reason"],
    ] for r in visible]


def refresh_batch_table(state: dict, sort_by: str, ood_filter: str):
    """排序/过滤控件回调：仅重渲染，不重跑预测。"""
    return _render_batch_rows((state or {}).get("rows", []), sort_by, ood_filter)


def export_batch_csv(state: dict) -> str | None:
    """导出当前批量结果为 CSV。"""
    rows = (state or {}).get("rows", [])
    if not rows:
        return None
    BATCH_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = BATCH_EXPORT_DIR / f"batch_rank_{datetime.now():%Y%m%d_%H%M%S}.csv"
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["醛SMILES", "胺SMILES", "醛名称", "胺名称",
                         "成膜打分（倾向性）", "std", "路由臂", "OOD状态", "Top理由"])
        for r in rows:
            writer.writerow([
                r["ald"], r["amine"], r["ald_name"], r["amine_name"],
                "" if r["score"] is None else f"{r['score']:.4f}",
                "" if r["std"] is None else f"{r['std']:.4f}",
                r["arm"], _ood_label(r["ood_level"]), r["reason"],
            ])
    return str(path)


# ---------------------------------------------------------------------------
# 主题与布局
# ---------------------------------------------------------------------------

CUSTOM_CSS = """
.app-header h1 { margin: 0; font-size: 1.6rem; }
.model-badge {
    display: inline-block; padding: 3px 12px; border-radius: 999px;
    background: #0f766e; color: #fff; font-size: 0.8rem; font-weight: 600;
    margin-left: 10px; vertical-align: middle;
}
.score-big { font-size: 2.6rem; font-weight: 700; line-height: 1.15; }
.score-std { font-size: 1.05rem; font-weight: 400; color: #64748b; }
.placeholder-page { padding: 24px; text-align: center; color: #64748b; }
/* 中文字体栈放在 CSS 里：theme 的 font= 字符串参数会触发 gradio 6.20
   launch() 主题比较 bug（fonts.py __eq__: 'str' object has no attribute 'name'） */
body, .gradio-container {
    font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", sans-serif;
}
"""


def _build_theme() -> gr.themes.Soft:
    """深青/石墨学术系主题（方案第 5 节）。

    注意：不要传 font= 字符串列表 —— gradio 6.20 的 launch() 会把本主题与
    内置主题逐个 to_dict() 比较，Font.__eq__ 遇到 str 直接 AttributeError
    （'str' object has no attribute 'name'），App 启动即崩。字体见 CUSTOM_CSS。
    """
    return gr.themes.Soft(
        primary_hue=gr.themes.colors.teal,
        secondary_hue=gr.themes.colors.cyan,
        neutral_hue=gr.themes.colors.slate,
    )


def create_app() -> gr.Blocks:
    """创建五标签页 Gradio App。"""
    lib = load_builtin_monomers()

    with gr.Blocks(title="COF 成膜单体推荐系统") as app:
        gr.Markdown(
            '<div class="app-header"><h1>COF 成膜单体推荐系统'
            f'<span class="model-badge">模型：{MODEL_VERSION_BADGE}</span>'
            "</h1></div>\n\n"
            "输入醛 + 胺单体，给出**成膜打分（倾向性）** ± 不确定度、SHAP 中文打分理由、"
            "推荐实验条件与 Word 报告。检出 OOD（非标准官能团 / 双未见 / 特征超分布）时"
            "以 ⚠️ / ⛔ 提示，⛔ 不输出分数。"
        )

        with gr.Tabs():
            # ===================== 页① 查询打分 =====================
            with gr.Tab("① 查询打分"):
                with gr.Row():
                    # ---- 输入卡片 ----
                    with gr.Column(scale=5):
                        with gr.Group():
                            gr.Markdown("#### 方式一：内置单体库点选（免输入）")
                            gr.Markdown(
                                f"*单体库来源：{lib['source']}；"
                                f"醛 {len(lib['aldehydes'])} 个 / 胺 {len(lib['amines'])} 个。*"
                            )
                            ald_pick = gr.Dropdown(
                                label="选择醛单体", choices=lib["aldehydes"],
                                interactive=True,
                            )
                            amine_pick = gr.Dropdown(
                                label="选择胺单体", choices=lib["amines"],
                                interactive=True,
                            )
                        with gr.Group():
                            gr.Markdown("#### 方式二：CAS 号查询")
                            with gr.Row():
                                cas_input = gr.Textbox(
                                    label="CAS 号", placeholder="例如 14544-47-9", scale=3)
                                cas_role = gr.Radio(["醛", "胺"], value="醛",
                                                    label="作为", scale=1)
                            cas_btn = gr.Button("解析并填入", size="sm")
                            cas_status = gr.Markdown()
                        with gr.Group():
                            gr.Markdown("#### 方式三：SMILES 直输")
                            ald_input = gr.Textbox(
                                label="醛单体 SMILES",
                                placeholder="例如：O=CC1=C(C=O)C(=O)C(C=O)=C1O",
                            )
                            amine_input = gr.Textbox(
                                label="胺单体 SMILES",
                                placeholder="例如：Nc1ccc(N)cc1",
                            )
                        predict_btn = gr.Button("开始打分", variant="primary", size="lg")
                        report_btn = gr.Button("生成 Word 实验报告")
                        report_output = gr.File(label="实验报告")

                    # ---- 输出卡片 ----
                    with gr.Column(scale=7):
                        with gr.Group():
                            prob_output = gr.Markdown(value=_EMPTY_HINT)
                        with gr.Group():
                            explain_output = gr.Markdown(
                                value="*打分理由（SHAP 归因）将在打分后显示。*")
                        with gr.Group():
                            similar_output = gr.Markdown(
                                value="*相似成膜案例将在打分后显示。*")
                        with gr.Group():
                            cond_output = gr.Markdown(
                                value="*推荐实验条件将在打分后显示。*")

                gr.Markdown("### 化学结构")
                with gr.Row():
                    ald_img_output = gr.Image(label="醛单体", height=260)
                    amine_img_output = gr.Image(label="胺单体", height=260)
                    product_img_output = gr.Image(
                        label="缩合产物骨架（亚胺键 C=N 示意）", height=260)
                struct_note_output = gr.Markdown()

                # 事件：库点选自动填入 / CAS 解析 / 预测 / 报告
                ald_pick.change(fn=lambda v: v or "", inputs=[ald_pick],
                                outputs=[ald_input])
                amine_pick.change(fn=lambda v: v or "", inputs=[amine_pick],
                                  outputs=[amine_input])
                cas_btn.click(fn=cas_fill, inputs=[cas_input, cas_role],
                              outputs=[ald_input, amine_input, cas_status])
                predict_btn.click(
                    fn=predict,
                    inputs=[ald_input, amine_input],
                    outputs=[prob_output, cond_output, gr.Textbox(visible=False),
                             explain_output, ald_img_output, amine_img_output,
                             product_img_output, struct_note_output, similar_output],
                )
                report_btn.click(fn=generate_report_callback,
                                 inputs=[ald_input, amine_input],
                                 outputs=[report_output])

            # ===================== 页② 批量排序 =====================
            with gr.Tab("② 批量排序"):
                with gr.Group():
                    gr.Markdown(
                        "#### 输入单体对（三种方式可叠加，合计上限 "
                        f"{MAX_BATCH_PAIRS} 对）")
                    with gr.Row():
                        batch_ald = gr.Dropdown(
                            label="内置醛单体（多选，与胺两两组合）",
                            choices=lib["aldehydes"], multiselect=True)
                        batch_amine = gr.Dropdown(
                            label="内置胺单体（多选）",
                            choices=lib["amines"], multiselect=True)
                    batch_pasted = gr.Textbox(
                        label="粘贴 SMILES 对", lines=4,
                        placeholder="每行一对：醛SMILES, 胺SMILES（# 开头为注释行）")
                    batch_csv = gr.File(label="或上传 CSV（前两列为醛/胺 SMILES）",
                                        file_types=[".csv"])
                    batch_btn = gr.Button("开始批量打分", variant="primary")
                    batch_status = gr.Markdown(
                        value="*尚未运行——选择或粘贴单体对后点击「开始批量打分」。*")

                with gr.Row():
                    sort_ctrl = gr.Radio(
                        ["按打分降序", "按打分升序", "按不确定度降序"],
                        value="按打分降序", label="排序")
                    filter_ctrl = gr.Radio(
                        ["全部", "隐藏 ⛔ 不适用", "仅看 ✓ 池内"],
                        value="全部", label="OOD 过滤")
                batch_state = gr.State({"rows": []})
                batch_table = gr.Dataframe(
                    headers=BATCH_HEADERS, value=[], interactive=False, wrap=True,
                    label="批量排序结果")
                with gr.Row():
                    export_btn = gr.Button("导出 CSV")
                    export_file = gr.File(label="排序表 CSV")

                batch_btn.click(
                    fn=batch_predict,
                    inputs=[batch_ald, batch_amine, batch_pasted, batch_csv],
                    outputs=[batch_state, batch_table, batch_status])
                sort_ctrl.change(fn=refresh_batch_table,
                                 inputs=[batch_state, sort_ctrl, filter_ctrl],
                                 outputs=[batch_table])
                filter_ctrl.change(fn=refresh_batch_table,
                                   inputs=[batch_state, sort_ctrl, filter_ctrl],
                                   outputs=[batch_table])
                export_btn.click(fn=export_batch_csv, inputs=[batch_state],
                                 outputs=[export_file])

            # ===================== 页③④⑤ 占位 =====================
            with gr.Tab("③ 收藏夹"):
                gr.Markdown(
                    '<div class="placeholder-page"><h3>🚧 即将上线（P2）</h3>'
                    "收藏单体组合、自动匹配训练文献、关联实验记录与方案卡。"
                    "</div>")
            with gr.Tab("④ 实验记录"):
                gr.Markdown(
                    '<div class="placeholder-page"><h3>🚧 即将上线（P2）</h3>'
                    "上传真实实验条件与结果，沉淀标准化记录，展示预测 vs 实际偏差。"
                    "</div>")
            with gr.Tab("⑤ 方案迭代"):
                gr.Markdown(
                    '<div class="placeholder-page"><h3>🚧 即将上线（P3）</h3>'
                    "对接 RAG 迭代：预测/收藏/实验记录按约定 schema 导出，"
                    "RAG 建议回显与收藏条目关联。</div>")

    # Gradio 6 把 theme/css 从 Blocks 构造器移到 launch()；构造器 kwargs 仅存入
    # _deprecated_theme/_deprecated_css 并告警。这里直接设置这两个内部字段，
    # 效果与构造器传入完全一致（launch 会回退读取），且无论谁启动 app 主题都生效。
    app._deprecated_theme = _build_theme()
    app._deprecated_css = CUSTOM_CSS
    return app


if __name__ == "__main__":
    app = create_app()
    app.launch(server_name="127.0.0.1", server_port=7860, share=False)
