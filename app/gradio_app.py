"""Gradio App 入口：COF 成膜单体推荐系统（P1 改版）。

五标签页架构（docs/APP_REDESIGN_PROPOSAL.md 第 2/3/5 节）：
- ① 查询打分：SMILES 直输 / CAS 号解析 / 内置单体库点选，输出打分±std、路由臂、
  GNN 对照、OOD ⛔/⚠️、SHAP 中文理由、结构图三件套、相似成膜案例、Word 报告。
- ② 批量排序：内置库多选（醛×胺笛卡尔组合）/ 粘贴 SMILES 对 / 上传 CSV →
  排序表（打分±std、路由臂、OOD、Top 理由一句话）→ 排序/过滤 → 导出 CSV。
- ③ 收藏夹：卡片墙 + 详情（预测快照、备注、文献自动匹配/手动添加、关联实验
  记录、重新打分、方案卡、删除）。
- ④ 实验记录：标准化录入表单（支持关联收藏 / 游离记录）+ 时间线
  （当初预测快照 vs 实际结果对比）。
- ⑤ 方案迭代（RAG 对接）：实验记录时间线摘要 + RAG 建议卡片回显
  （src.rag.suggestions，未就位时优雅降级为占位提示）。

打分口径（两模型较高值）：主展示分数 = max(路由树模型分, GNN 分)（±std）；
仅一方出分时用出分者并标注来源；都未出分显示未出分提示；属乐观召回口径，
高分请结合 OOD 与不确定度判断；OOD=out ⛔ 时一律不出分（⛔ 优先于打分）。
综合分为两者平均，仅对照展示并明确标注。

依赖的并行后端模块均为懒加载 + 优雅降级：模块未就位时对应板块显示提示而
不报错。P1 后端：src/utils/cas_lookup.py、src/utils/predict_log.py、
src/recommend/similar_cases.py；P2 后端（任务1）：src/favorites/store.py、
src/records/store.py、src/recommend/plan_card.py。
"""

from __future__ import annotations

import base64
import csv
import html
import io
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

# 页① 最近一次打分的快照（本地单用户 App，模块级即可）：
# {"ald": ..., "amine": ..., "pred": FilmPredictor.predict 的原始返回}
# 「☆ 收藏这组单体」时若 SMILES 对匹配则随收藏写入预测快照。
_LAST_PREDICTION: dict = {}


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


def _headline_score(pred_result: dict) -> tuple[float | None, str | None]:
    """主分数统一口径（两模型较高值）：主展示分数 = max(路由树模型分, GNN 分)。

    返回 (score, source)：source ∈ {"both", "tree", "gnn", None}——
    两模型均出分时取较高者（source="both"）；仅一方出分时用出分者，
    由展示层标注来源；都未出分返回 (None, None)，由展示层明确提示。

    属乐观召回口径：高分需结合 OOD 与不确定度判断；OOD=out 时由调用方
    保证不出分（⛔ 优先于打分）。综合分（两者平均）仅对照展示，不作主分数。
    """
    pred_result = pred_result or {}
    tree = pred_result.get("tree_probability")
    gnn = pred_result.get("gnn_probability")
    tree = tree if isinstance(tree, (int, float)) else None
    gnn = gnn if isinstance(gnn, (int, float)) else None
    if tree is not None and gnn is not None:
        return max(tree, gnn), "both"
    if tree is not None:
        return tree, "tree"
    if gnn is not None:
        return gnn, "gnn"
    return None, None


def _build_log_record(ald_smiles: str, amine_smiles: str, pred_result: dict,
                      source: str) -> dict:
    """按数据契约（方案第 6 节）组装 prediction 日志记录。

    score 为主分数（两模型较高值口径，score_policy 注明）；tree_score /
    gnn_score 保留分量便于溯源；ood.level=="out" 时 score 置 null（⛔ 优先于
    打分，契约要求）。"""
    ood = pred_result.get("ood") or {}
    score, _ = _headline_score(pred_result)
    return {
        "schema_version": "1.0",
        "type": "prediction",
        "ald_smiles": ald_smiles,
        "amine_smiles": amine_smiles,
        "score": None if ood.get("level") == "out" else score,
        "score_policy": "max_tree_gnn",
        "tree_score": pred_result.get("tree_probability"),
        "gnn_score": pred_result.get("gnn_probability"),
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
    _LAST_PREDICTION.update(
        {"ald": ald_smiles, "amine": amine_smiles, "pred": pred_result})

    # 预测日志（D23 路由复盘 / 使用统计）
    _log_prediction(_build_log_record(ald_smiles, amine_smiles, pred_result, "single"))

    # 条件推荐
    conditions = recommend(ald_smiles, amine_smiles)

    # OOD 状态（三级制，D27）：out → 不显示分数，显示「模型不适用」+ 原因
    ood = pred_result.get("ood") or {}
    ood_out = ood.get("level") == "out"

    # 格式化打分输出（口径：倾向性打分，非严格概率——论文口径 D27；
    # 主分数 = 两模型较高值 max(树模型, GNN)，乐观召回口径，附护栏标注）
    prob_text = ""
    if not ood_out:
        main_score, main_src = _headline_score(pred_result)
        if main_score is not None:
            src_tag = {"both": "两模型较高值",
                       "tree": "两模型较高值 · 仅树模型出分",
                       "gnn": "两模型较高值 · 仅 GNN 出分"}[main_src]
            prob_text += _big_score_html(main_score, pred_result.get("score_std"))
            prob_text += f'<div class="score-tag">{src_tag}</div>\n\n'
        else:
            prob_text += ("> ⚠️ 树模型与 GNN 均未出分——主分数（两模型较高值口径）"
                          "无可用来源，不出分（各模型状态见下）。\n\n")
    prob_text += "### 成膜打分（倾向性）\n\n"
    prob_text += "> 四级软标签上的倾向性打分，非严格概率；对反应条件不敏感。\n\n"
    prob_text += ("> 主分数取两模型较高者，属乐观召回口径，"
                  "高分请结合 OOD 与不确定度判断。\n\n")
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
            prob_text += (f"- **综合打分（树与 GNN 平均，仅对照参考）**: "
                          f"{pred_result['ensemble_probability']:.3f}")
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

BATCH_HEADERS = ["醛", "胺", "成膜打分（倾向性·较高值）", "±std", "路由臂", "OOD", "Top 理由"]


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
            score, score_src = _headline_score(pred)
            std = pred.get("score_std")
            if score is None:
                reason = "两模型均未出分（较高值口径无来源，不出分）"
            else:
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
                         "成膜打分（倾向性·较高值）", "std", "路由臂", "OOD状态", "Top理由"])
        for r in rows:
            writer.writerow([
                r["ald"], r["amine"], r["ald_name"], r["amine_name"],
                "" if r["score"] is None else f"{r['score']:.4f}",
                "" if r["std"] is None else f"{r['std']:.4f}",
                r["arm"], _ood_label(r["ood_level"]), r["reason"],
            ])
    return str(path)


# ---------------------------------------------------------------------------
# 任务1 后端模块的安全封装（收藏夹 / 实验记录 / 方案卡；未就位时优雅降级）
# 所有封装统一返回 (payload, 错误文案)；payload 为 None 时错误文案必有值。
# ---------------------------------------------------------------------------

def _load_favorites_store():
    try:
        from favorites import store as fav_store
        return fav_store, None
    except ImportError:
        return None, "⏳ 收藏夹后端模块尚未上线（后端开发中），本功能暂不可用。"


def _load_records_store():
    try:
        from records import store as rec_store
        return rec_store, None
    except ImportError:
        return None, "⏳ 实验记录后端模块尚未上线（后端开发中），本功能暂不可用。"


def _fav_add(ald, amine, ald_name, amine_name, notes):
    store, err = _load_favorites_store()
    if err:
        return None, err
    try:
        return store.add_favorite(ald, amine, ald_name, amine_name, notes), None
    except Exception as e:
        return None, f"⚠️ 收藏失败：{_brief_error(e)}"


def _fav_list():
    store, err = _load_favorites_store()
    if err:
        return None, err
    try:
        return store.list_favorites() or [], None
    except Exception as e:
        return None, f"⚠️ 收藏列表读取失败：{_brief_error(e)}"


def _fav_get(fid):
    store, err = _load_favorites_store()
    if err:
        return None, err
    try:
        fav = store.get_favorite(fid)
    except Exception as e:
        return None, f"⚠️ 收藏条目读取失败：{_brief_error(e)}"
    if not fav:
        return None, f"⚠️ 收藏条目 {fid} 不存在（可能已被删除）。"
    return fav, None


def _fav_update(fid, **fields):
    store, err = _load_favorites_store()
    if err:
        return None, err
    try:
        return store.update_favorite(fid, **fields), None
    except Exception as e:
        return None, f"⚠️ 收藏更新失败：{_brief_error(e)}"


def _fav_delete(fid):
    store, err = _load_favorites_store()
    if err:
        return None, err
    try:
        store.delete_favorite(fid)
        return True, None
    except Exception as e:
        return None, f"⚠️ 删除失败：{_brief_error(e)}"


def _snapshot_payload(pred: dict) -> dict:
    """FilmPredictor 原始返回 → 任务1 update_prediction_snapshot 期望的快照口径。

    score 为主分数（两模型较高值，score_policy 注明口径）；tree_score /
    gnn_score 保留分量便于溯源；ood 为 level 字符串；⛔ out 时 score=None。"""
    pred = pred or {}
    ood = pred.get("ood") or {}
    level = ood.get("level", "none") if isinstance(ood, dict) else str(ood or "none")
    score, _ = _headline_score(pred)
    return {
        "score": None if level == "out" else score,
        "std": pred.get("score_std"),
        "arm": pred.get("tree_model_name", ""),
        "ood": level,
        "score_policy": "max_tree_gnn",
        "tree_score": pred.get("tree_probability"),
        "gnn_score": pred.get("gnn_probability"),
    }


def _fav_update_snapshot(fid, pred):
    store, err = _load_favorites_store()
    if err:
        return None, err
    try:
        store.update_prediction_snapshot(fid, _snapshot_payload(pred))
        return True, None
    except Exception as e:
        return None, f"⚠️ 预测快照写入失败：{_brief_error(e)}"


def _fav_add_ref(fid, title, doi, url_or_path, note):
    store, err = _load_favorites_store()
    if err:
        return None, err
    try:
        return store.add_reference(fid, title, doi, url_or_path, note), None
    except Exception as e:
        return None, f"⚠️ 文献添加失败：{_brief_error(e)}"


def _fav_auto_refs(ald, amine, max_refs=8):
    store, err = _load_favorites_store()
    if err:
        return None, err
    try:
        return store.auto_match_references(ald, amine, max_refs=max_refs) or [], None
    except Exception as e:
        return None, f"⚠️ 文献自动匹配失败：{_brief_error(e)}"


def _rec_create_linked(favorite_id, conditions, outcome, strength, notes, operator):
    """关联收藏的实验记录（单体对象与预测快照由后端从收藏冗余）。"""
    store, err = _load_records_store()
    if err:
        return None, err
    try:
        return store.create_record(
            favorite_id=favorite_id, conditions=conditions, outcome=outcome,
            strength=strength, notes=notes, operator=operator), None
    except Exception as e:
        return None, f"⚠️ 实验记录保存失败：{_brief_error(e)}"


def _rec_create_free(aldehyde_smiles, amine_smiles, conditions, outcome,
                     strength, notes, operator):
    """游离实验记录（不关联收藏，醛/胺 SMILES 直接录入；签名与任务B钉死）。"""
    store, err = _load_records_store()
    if err:
        return None, err
    try:
        return store.create_record(
            favorite_id=None, aldehyde_smiles=aldehyde_smiles,
            amine_smiles=amine_smiles, conditions=conditions, outcome=outcome,
            strength=strength, notes=notes, operator=operator), None
    except TypeError:
        return None, "⏳ 游离记录需后端扩展签名支持（任务B开发中），请改用关联收藏录入。"
    except Exception as e:
        return None, f"⚠️ 实验记录保存失败：{_brief_error(e)}"


def _rec_list(favorite_id=None):
    store, err = _load_records_store()
    if err:
        return None, err
    try:
        return store.list_records(favorite_id=favorite_id) or [], None
    except Exception as e:
        return None, f"⚠️ 实验记录读取失败：{_brief_error(e)}"


def _plan_generate(ald, amine, ald_name, amine_name):
    try:
        from recommend.plan_card import generate_plan_card
    except ImportError:
        return None, "⏳ 方案卡模块尚未上线（后端开发中）。"
    try:
        return generate_plan_card(ald, amine, ald_name, amine_name), None
    except Exception as e:
        return None, f"⚠️ 方案卡生成失败：{_brief_error(e)}"


# ---------------------------------------------------------------------------
# P2 展示辅助：收藏卡片墙 / 方案卡 / 文献 / 实验记录时间线
# ---------------------------------------------------------------------------

def _esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def _smiles_img_b64(smiles: str, size=(220, 150)) -> str:
    """SMILES → base64 data URI（嵌卡片墙）；解析失败返回空串。"""
    if not smiles:
        return ""
    img = smiles_to_image(smiles, size=size)
    if img is None:
        return ""
    buf = io.BytesIO()
    try:
        img.save(buf, format="PNG")
    except Exception:
        return ""
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _fav_pair(fav: dict) -> tuple[str, str, str, str]:
    """从收藏条目提取 (醛SMILES, 胺SMILES, 醛名, 胺名)，宽容兼容任务1 schema：
    单体可能是 {"smiles","name"} 对象（数据契约形态）或平铺 ald_smiles 字段。
    """
    def _mon(obj_key: str, flat_prefix: str) -> tuple[str, str]:
        obj = fav.get(obj_key)
        if isinstance(obj, dict):
            return obj.get("smiles") or "", obj.get("name") or ""
        if isinstance(obj, str):
            return obj, ""
        return (fav.get(f"{flat_prefix}_smiles") or fav.get(flat_prefix) or "",
                fav.get(f"{flat_prefix}_name") or "")

    ald_s, ald_n = _mon("aldehyde", "ald")
    amine_s, amine_n = _mon("amine", "amine")
    name_map = load_builtin_monomers()["name_by_smiles"]
    ald_n = ald_n or (_display_name(ald_s, name_map) if ald_s else "?")
    amine_n = amine_n or (_display_name(amine_s, name_map) if amine_s else "?")
    return ald_s, amine_s, ald_n, amine_n


def _fav_label(fav: dict) -> str:
    _, _, ald_n, amine_n = _fav_pair(fav)
    return f"{ald_n} × {amine_n} · {fav.get('id', '?')}"


def _fav_snapshot(fav: dict) -> tuple:
    """提取 (score, std, ood_level, date)；ood 兼容字符串或 {"level"} 对象。"""
    snap = fav.get("latest_prediction") or fav.get("prediction_snapshot") or {}
    score = snap.get("score")
    std = snap.get("std", snap.get("score_std"))
    ood = snap.get("ood", "none")
    if isinstance(ood, dict):
        ood = ood.get("level", "none")
    date = snap.get("date") or snap.get("timestamp") or ""
    return score, std, ood, date


def _score_badge_html(score, std, ood_level: str) -> str:
    """收藏卡片上的打分徽章，沿用页① 色彩语义（青/琥珀/红 + OOD）。"""
    if ood_level == "out":
        return '<span class="fav-badge" style="background:#b91c1c">⛔ 不适用</span>'
    if not isinstance(score, (int, float)):
        return '<span class="fav-badge" style="background:#64748b">未打分</span>'
    std_txt = f"±{std:.3f} " if isinstance(std, (int, float)) else ""
    warn = " ⚠️" if ood_level == "warning" else ""
    return (f'<span class="fav-badge" style="background:{_score_color(score)}">'
            f"{score:.3f} {std_txt}{warn}</span>")


def _render_favorite_cards(favs: list) -> str:
    """收藏卡片墙：结构缩略图 + 名称 + 最新打分徽章 + OOD 状态。"""
    if not favs:
        return ('<div class="placeholder-page">收藏夹还是空的——'
                "到页① 打分后点击「☆ 收藏这组单体」。</div>")
    cards = []
    for fav in favs:
        ald_s, amine_s, ald_n, amine_n = _fav_pair(fav)
        score, std, ood_level, _ = _fav_snapshot(fav)
        imgs = ""
        for s in (ald_s, amine_s):
            uri = _smiles_img_b64(s)
            if uri:
                imgs += f'<img src="{uri}" alt="单体结构">'
        notes = _esc((fav.get("notes") or "")[:60])
        meta = _esc(str(fav.get("id", "?"))) + (f" · {notes}" if notes else "")
        cards.append(
            '<div class="fav-card">'
            f'<div class="fav-card-imgs">{imgs}</div>'
            f'<div class="fav-card-title">{_esc(ald_n)} × {_esc(amine_n)}</div>'
            f"<div>{_score_badge_html(score, std, ood_level)}</div>"
            f'<div class="fav-card-meta">{meta}</div>'
            "</div>")
    return ('<div class="fav-wall">' + "".join(cards) + "</div>"
            '<div class="fav-wall-hint">👇 在下方「收藏详情」选择条目，'
            "查看快照 / 文献 / 实验记录。</div>")


def _render_plan_card_html(card: dict, ald_name: str = "", amine_name: str = "") -> str:
    """方案卡 HTML：条件表 + 加料顺序 + ⛔ 防错清单 + 单体特异提示。

    宽容兼容任务1 schema：conditions 接受 dict 或 [{param,value}] 列表。
    """
    if not card:
        return ""
    title = card.get("title") or f"实验方案卡：{ald_name} × {amine_name}"
    template = card.get("template") or ""
    parts = [f'<div class="plan-card"><h3>🧪 {_esc(title)}</h3>']
    if template:
        parts.append(f'<div class="plan-template">模板：{_esc(template)}'
                     + (f"（{_esc(card['defaults_note'])}）" if card.get("defaults_note") else "")
                     + "</div>")

    conds = card.get("conditions") or {}
    rows = ""
    if isinstance(conds, dict):
        rows = "".join(
            f"<tr><td>{_esc(_COND_LABELS.get(k, k))}</td><td>{_esc(v)}</td></tr>"
            for k, v in conds.items())
    elif isinstance(conds, list):
        for c in conds:
            if isinstance(c, dict):
                rows += (f"<tr><td>{_esc(c.get('param') or c.get('name') or '')}</td>"
                         f"<td>{_esc(c.get('value') or '')}</td></tr>")
            else:
                rows += f'<tr><td colspan="2">{_esc(c)}</td></tr>'
    if rows:
        parts.append(f'<h4>条件参数</h4><table class="plan-table">{rows}</table>')

    steps = card.get("steps") or []
    if steps:
        items = "".join(f"<li>{_esc(s)}</li>" for s in steps)
        parts.append(f"<h4>加料顺序与操作要点</h4><ol>{items}</ol>")

    checklist = card.get("checklist") or []
    if checklist:
        items = ""
        for c in checklist:
            if isinstance(c, dict):  # 任务1 schema：{item, detail}
                items += (f"<li><b>{_esc(c.get('item') or '')}</b>"
                          f"——{_esc(c.get('detail') or '')}</li>")
            else:
                items += f"<li>{_esc(c)}</li>"
        parts.append(f'<h4>⛔ 防错清单（逐条核对后再开反应）</h4>'
                     f'<ul class="plan-checklist">{items}</ul>')

    hints = card.get("monomer_hints") or []
    if isinstance(hints, str):
        hints = [hints]
    if hints:
        items = "".join(f"<li>{_esc(h)}</li>" for h in hints)
        parts.append(f"<h4>单体特异提示</h4><ul>{items}</ul>")

    parts.append("</div>")
    return "".join(parts)


def _resolve_ref_title(paper_id) -> str | None:
    """auto-matched 文献标题解析（任务B src.references.titles.resolve_title）。

    模块未就位 / 解析失败 / 返回 None 或原名时一律返回 None，调用方回退
    显示 paper_id。
    """
    pid = str(paper_id or "").strip()
    if not pid:
        return None
    try:
        from references.titles import resolve_title
    except ImportError:
        return None
    try:
        title = resolve_title(pid)
    except Exception:
        return None
    if not title or str(title) == pid:
        return None
    return str(title)


def _render_refs_html(fav: dict, auto_refs: list | None = None) -> str:
    """文献列表：收藏条目内已挂文献 + （可选）即时自动匹配结果。

    自动匹配的标注「相关文献·自动匹配」（方案 8.2：反查到的是报道过该单体
    的文献，与支撑该组合不完全等同，措辞为相关文献）；auto-matched 条目
    标题经 resolve_title 解析为文献标题，解析不到时回退显示 paper_id。
    """
    def _ref_li(r: dict, tag: str, tag_cls: str, resolve: bool = False) -> str:
        raw_title = r.get("title") or r.get("paper_id") or "（无标题）"
        resolved = _resolve_ref_title(raw_title) if resolve else None
        if resolved:
            line = (f"<b>{_esc(resolved)}</b> "
                    f'<span class="ref-pid">paper_id: {_esc(raw_title)}</span>')
        else:
            line = f"<b>{_esc(raw_title)}</b>"
        doi = r.get("doi") or ""
        if doi:
            line += f" · DOI: {_esc(doi)}"
        link = r.get("url_or_path") or r.get("path_or_url") or ""
        if link:
            if str(link).startswith("http"):
                line += f' · <a href="{_esc(link)}" target="_blank">链接</a>'
            else:
                line += f" · {_esc(link)}"
        note = r.get("note") or ""
        if note:
            line += f"<br><i>{_esc(note)}</i>"
        return f'{line} <span class="ref-tag {tag_cls}">{tag}</span>'

    items = []
    for r in fav.get("references") or []:
        src = r.get("source") or "user-added"
        if src == "auto-matched":
            items.append(f"<li>{_ref_li(r, '相关文献·自动匹配', 'auto', resolve=True)}</li>")
        else:
            items.append(f"<li>{_ref_li(r, '手动添加', '')}</li>")
    for r in auto_refs or []:
        items.append(f"<li>{_ref_li(r, '相关文献·自动匹配', 'auto', resolve=True)}</li>")
    if not items:
        return ("<i>暂无文献——点击「自动匹配相关文献」按醛/胺反查训练语料，"
                "或手动填写标题/DOI 添加。</i>")
    return "<ul>" + "".join(items) + "</ul>"


_OUTCOME_ZH = {
    "film": ("✓ 成膜", "#0f766e"),
    "partial": ("⚠️ 部分成膜", "#b45309"),
    "failed": ("⛔ 失败", "#b91c1c"),
}
_COND_LABELS = {
    "solvent": "溶剂", "modulator": "调制剂", "catalyst": "催化剂",
    "temperature_c": "温度(°C)", "time_days": "时间(天)",
    "vessel": "容器", "addition_order": "加料顺序",
}


def _record_pair_names(rec: dict) -> str:
    """记录条目的单体对显示名：优先记录内嵌单体对象，缺失时回查收藏条目。"""
    ald, amine = rec.get("aldehyde"), rec.get("amine")
    if isinstance(ald, dict) and isinstance(amine, dict):
        name_map = load_builtin_monomers()["name_by_smiles"]
        a = ald.get("name") or _display_name(ald.get("smiles", "?"), name_map)
        b = amine.get("name") or _display_name(amine.get("smiles", "?"), name_map)
        return f"{a} × {b}"
    fid = rec.get("favorite_id")
    if fid:
        fav, err = _fav_get(fid)
        if fav:
            _, _, ald_n, amine_n = _fav_pair(fav)
            return f"{ald_n} × {amine_n}"
    return "（游离记录）"


def _render_records_timeline(records: list) -> str:
    """实验记录时间线：每条显示当初预测快照 vs 实际结果的对比。"""
    if not records:
        return '<div class="placeholder-page">暂无实验记录。</div>'
    items = []
    for rec in records:
        label, color = _OUTCOME_ZH.get(rec.get("outcome"), (rec.get("outcome") or "—", "#64748b"))
        date = rec.get("date") or str(rec.get("created_at") or "")[:10] or "?"
        pair = _esc(_record_pair_names(rec))

        snap = rec.get("prediction_snapshot") or {}
        snap_score, snap_std = snap.get("score"), snap.get("std", snap.get("score_std"))
        snap_ood = snap.get("ood", "none")
        if isinstance(snap_ood, dict):
            snap_ood = snap_ood.get("level", "none")
        if snap_ood == "out":
            pred_txt = "预测：⛔ OOD 不适用"
        elif isinstance(snap_score, (int, float)):
            pred_txt = f"预测 {snap_score:.3f}"
            if isinstance(snap_std, (int, float)):
                pred_txt += f" ± {snap_std:.3f}"
            pred_txt += f"（{_ood_label(snap_ood)}）"
        else:
            pred_txt = "无预测快照"
        compare = (f'<div class="rec-compare">{_esc(pred_txt)}'
                   f" → 实际：<b style=\"color:{color}\">{label}</b></div>")

        conds = rec.get("conditions") or {}
        cond_txt = "；".join(
            f"{_COND_LABELS.get(k, k)}：{_esc(v)}" for k, v in conds.items()
            if v not in (None, ""))
        cond_html = f'<div class="rec-conds">{cond_txt}</div>' if cond_txt else ""

        rid = rec.get("record_id") or rec.get("id") or "?"
        meta_parts = []
        if rec.get("strength"):
            meta_parts.append(f"机械强度：{_esc(rec['strength'])}")
        if rec.get("operator"):
            meta_parts.append(f"操作人：{_esc(rec['operator'])}")
        meta_parts.append(f"<code>{_esc(rid)}</code>")
        notes_html = (f'<div class="rec-notes">备注：{_esc(rec["notes"])}</div>'
                      if rec.get("notes") else "")

        items.append(
            '<div class="rec-item">'
            f'<div class="rec-head"><span class="rec-date">{_esc(date)}</span> '
            f"<b>{pair}</b> "
            f'<span class="rec-outcome" style="background:{color}">{label}</span></div>'
            f"{compare}{cond_html}"
            f'<div class="rec-meta">{" · ".join(meta_parts)}</div>'
            f"{notes_html}</div>")
    return '<div class="rec-timeline">' + "".join(items) + "</div>"


# ---------------------------------------------------------------------------
# 页① 增强回调：收藏 + 方案卡
# ---------------------------------------------------------------------------

def _canonical_smiles(smiles: str) -> str | None:
    """RDKit 规范化 SMILES（与收藏后端同口径）；解析失败返回 None。"""
    try:
        from rdkit import Chem
        mol = Chem.MolFromSmiles((smiles or "").strip())
        return Chem.MolToSmiles(mol) if mol else None
    except Exception:
        return None


def _find_favorite_by_pair(ald_smiles: str, amine_smiles: str, favs: list) -> dict | None:
    """按规范化 SMILES 对在收藏列表中查重；命中返回该收藏条目。"""
    canon_ald = _canonical_smiles(ald_smiles) or (ald_smiles or "").strip()
    canon_amine = _canonical_smiles(amine_smiles) or (amine_smiles or "").strip()
    for fav in favs or []:
        f_ald, f_amine, _, _ = _fav_pair(fav)
        if ((_canonical_smiles(f_ald) or f_ald) == canon_ald
                and (_canonical_smiles(f_amine) or f_amine) == canon_amine):
            return fav
    return None


def favorite_current(ald_smiles: str, amine_smiles: str, notes: str) -> str:
    """「☆ 收藏这组单体」：先查重——同 SMILES 对已收藏过则更新快照而非新建。"""
    if not ald_smiles.strip() or not amine_smiles.strip():
        return "⚠️ 请先填写醛和胺的 SMILES 再收藏。"
    ald_smiles, amine_smiles = ald_smiles.strip(), amine_smiles.strip()
    name_map = load_builtin_monomers()["name_by_smiles"]
    ald_name = _display_name(ald_smiles, name_map)
    amine_name = _display_name(amine_smiles, name_map)

    # 去重：同 SMILES 对已存在 → 走 update_prediction_snapshot，不重复新建
    favs, _ = _fav_list()
    existing = _find_favorite_by_pair(ald_smiles, amine_smiles, favs) if favs else None
    if existing:
        fid = existing.get("id", "?")
        msg = f"⚠️ 已收藏过「{ald_name} × {amine_name}」（{fid}）"
        snap = _LAST_PREDICTION
        if snap.get("ald") == ald_smiles and snap.get("amine") == amine_smiles:
            _, serr = _fav_update_snapshot(fid, snap.get("pred") or {})
            msg += "，已更新快照。" if not serr else f"。（{serr}）"
        else:
            msg += "——未重复收藏；可在页③ 详情中「重新打分」刷新快照。"
        if (notes or "").strip():
            _, nerr = _fav_update(fid, notes=(notes or "").strip())
            msg += " 备注已更新。" if not nerr else f"（{nerr}）"
        return msg

    fav, err = _fav_add(ald_smiles, amine_smiles, ald_name, amine_name,
                        (notes or "").strip())
    if err:
        return err
    msg = f"✓ 已收藏「{ald_name} × {amine_name}」（{fav.get('id', '?')}），可到页③ 收藏夹查看。"
    snap = _LAST_PREDICTION
    if snap.get("ald") == ald_smiles and snap.get("amine") == amine_smiles:
        _, serr = _fav_update_snapshot(fav.get("id"), snap.get("pred") or {})
        msg += " 已附带当前打分快照。" if not serr else f"（{serr}）"
    else:
        msg += "（当前无该组合的打分快照——可在页③ 详情中「重新打分」。）"
    return msg


def plan_card_for_input(ald_smiles: str, amine_smiles: str):
    """页①「生成实验方案卡」：直接按当前 SMILES 对生成。"""
    if not ald_smiles.strip() or not amine_smiles.strip():
        return "", "⚠️ 请先填写醛和胺的 SMILES。"
    name_map = load_builtin_monomers()["name_by_smiles"]
    ald_name = _display_name(ald_smiles.strip(), name_map)
    amine_name = _display_name(amine_smiles.strip(), name_map)
    card, err = _plan_generate(ald_smiles.strip(), amine_smiles.strip(),
                               ald_name, amine_name)
    if err:
        return "", err
    return (_render_plan_card_html(card, ald_name, amine_name),
            "✓ 方案卡已生成——防错清单来自历史失败教训，请逐条核对后再开反应。")


# ---------------------------------------------------------------------------
# 页③ 收藏夹：回调
# ---------------------------------------------------------------------------

def refresh_favorites():
    """刷新卡片墙 + 详情下拉选项。返回 (cards_html, select_update, status)。"""
    favs, err = _fav_list()
    if err:
        return (f'<div class="placeholder-page">{_esc(err)}</div>',
                gr.update(choices=[], value=None), err)
    choices = [(_fav_label(f), f.get("id")) for f in favs]
    status = f"共 {len(favs)} 条收藏。" if favs else "收藏夹为空。"
    return _render_favorite_cards(favs), gr.update(choices=choices, value=None), status


def _snapshot_markdown(fav: dict) -> str:
    score, std, ood_level, date = _fav_snapshot(fav)
    date_txt = f"（{date}）" if date else ""
    snap = fav.get("latest_prediction") or fav.get("prediction_snapshot") or {}
    policy_txt = ("　口径：两模型较高值"
                  if snap.get("score_policy") == "max_tree_gnn" else "")
    if ood_level == "out":
        return f"**最新预测快照**：⛔ OOD 不适用（不出分）{date_txt}"
    if not isinstance(score, (int, float)):
        return "*尚无预测快照——可点击下方「重新打分」。*"
    return (f"**最新预测快照**：{_big_score_html(score, std)}{policy_txt}　"
            f"OOD：{_ood_label(ood_level)}{date_txt}")


def _pred_snapshot_markdown(pred: dict) -> str:
    """由 FilmPredictor 原始返回渲染快照（重新打分后立即展示用）。"""
    ood = pred.get("ood") or {}
    level = ood.get("level", "none")
    if level == "out":
        return "**最新预测快照**：⛔ OOD 不适用（不出分）（刚刚）"
    score, _ = _headline_score(pred)
    if not isinstance(score, (int, float)):
        return "*重新打分未出分（两模型均未出分）。*"
    return (f"**最新预测快照**：{_big_score_html(score, pred.get('score_std'))}　"
            f"口径：两模型较高值　OOD：{_ood_label(level)}（刚刚）")


def show_favorite_detail(fid: str):
    """选中收藏条目 → (信息, 快照, 备注值, 文献HTML, 关联记录HTML)。"""
    if not fid:
        return ("*请先在上方选择收藏条目。*", "", "",
                "<i>暂无文献。</i>", "<i>暂无记录。</i>")
    fav, err = _fav_get(fid)
    if err:
        return (err, "", "", "", "")
    ald_s, amine_s, ald_n, amine_n = _fav_pair(fav)
    info = (f"### {_esc(ald_n)} × {_esc(amine_n)}\n\n"
            f"- 收藏 ID：`{fav.get('id', '?')}`\n"
            f"- 创建时间：{fav.get('created_at', '?')}\n"
            f"- 醛 SMILES：`{ald_s}`\n"
            f"- 胺 SMILES：`{amine_s}`")
    recs, _ = _rec_list(favorite_id=fid)
    return (info, _snapshot_markdown(fav), fav.get("notes") or "",
            _render_refs_html(fav), _render_records_timeline(recs or []))


def save_favorite_notes(fid: str, notes: str) -> str:
    if not fid:
        return "⚠️ 请先选择收藏条目。"
    _, err = _fav_update(fid, notes=notes or "")
    return err or "✓ 备注已保存。"


def add_favorite_reference(fid: str, title: str, doi: str, url_or_path: str, note: str):
    """手动添加文献 → (状态, 文献HTML)。"""
    if not fid:
        return "⚠️ 请先选择收藏条目。", ""
    if not (title or "").strip():
        return "⚠️ 文献标题不能为空。", ""
    _, err = _fav_add_ref(fid, title.strip(), (doi or "").strip(),
                          (url_or_path or "").strip(), (note or "").strip())
    if err:
        return err, ""
    fav, ferr = _fav_get(fid)
    return "✓ 文献已添加。", (_render_refs_html(fav) if fav else (ferr or ""))


def auto_match_favorite_refs(fid: str):
    """自动匹配文献 → (文献HTML, 状态)。"""
    if not fid:
        return "", "⚠️ 请先选择收藏条目。"
    fav, err = _fav_get(fid)
    if err:
        return "", err
    ald_s, amine_s, _, _ = _fav_pair(fav)
    refs, rerr = _fav_auto_refs(ald_s, amine_s)
    if rerr:
        return "", rerr
    return (_render_refs_html(fav, auto_refs=refs),
            f"✓ 自动匹配到 {len(refs)} 篇相关文献（标注「相关文献·自动匹配」）。")


def rescore_favorite(fid: str):
    """重新打分并写回快照 → (状态, 快照Markdown)。"""
    if not fid:
        return "⚠️ 请先选择收藏条目。", ""
    fav, err = _fav_get(fid)
    if err:
        return err, ""
    ald_s, amine_s, _, _ = _fav_pair(fav)
    try:
        pred = _get_predictor().predict(ald_s, amine_s)
    except Exception as e:
        return f"⚠️ 打分失败：{_brief_error(e)}", ""
    _log_prediction(_build_log_record(ald_s, amine_s, pred, "single"))
    _, serr = _fav_update_snapshot(fid, pred)
    if serr:
        return serr, ""
    return "✓ 已重新打分并更新快照。", _pred_snapshot_markdown(pred)


def plan_card_for_favorite(fid: str):
    """页③「生成方案卡」→ (方案卡HTML, 状态)。"""
    if not fid:
        return "", "⚠️ 请先选择收藏条目。"
    fav, err = _fav_get(fid)
    if err:
        return "", err
    ald_s, amine_s, ald_n, amine_n = _fav_pair(fav)
    card, perr = _plan_generate(ald_s, amine_s, ald_n, amine_n)
    if perr:
        return "", perr
    return _render_plan_card_html(card, ald_n, amine_n), "✓ 方案卡已生成。"


def delete_favorite(fid: str):
    """删除收藏 → (状态, 卡片墙, 下拉, 详情清空×5)。"""
    empty_detail = ("", "", "", "", "")
    if not fid:
        return ("⚠️ 请先选择收藏条目。", "", gr.update()) + empty_detail
    _, err = _fav_delete(fid)
    if err:
        return (err, "", gr.update()) + empty_detail
    cards, sel, _ = refresh_favorites()
    return (f"✓ 已删除收藏 {fid}。", cards, sel) + empty_detail


# ---------------------------------------------------------------------------
# 页④ 实验记录：回调
# ---------------------------------------------------------------------------

_OUTCOME_MAP = {"成膜": "film", "部分成膜": "partial", "失败": "failed"}


def _record_fav_choices():
    favs, _ = _fav_list()
    choices = [(_fav_label(f), f.get("id")) for f in (favs or [])]
    return gr.update(choices=choices)


def refresh_records_tab():
    """页④ 进入/刷新：更新收藏下拉 + 记录时间线。"""
    recs, err = _rec_list()
    recs_html = (f'<div class="placeholder-page">{_esc(err)}</div>'
                 if err else _render_records_timeline(recs or []))
    return _record_fav_choices(), recs_html


def submit_record(fav_id, solvent, modulator, catalyst, temperature, time_days,
                  addition_order, outcome_zh, strength, operator, notes,
                  free_record=False, free_ald="", free_amine=""):
    """录入实验记录 → (状态, 记录时间线, 收藏下拉更新)。

    两种模式：
    - 关联收藏（默认）：记录挂在收藏条目上，单体与预测快照由后端冗余带入；
    - 游离记录（勾选「不关联收藏」）：favorite_id=None，直接录入醛/胺 SMILES。
    """
    conditions = {
        "solvent": (solvent or "").strip(),
        "modulator": (modulator or "").strip(),
        "catalyst": (catalyst or "").strip(),
        "temperature_c": (temperature or "").strip(),
        "time_days": (time_days or "").strip(),
        "addition_order": (addition_order or "").strip(),
    }
    if not any(conditions.values()) and not (notes or "").strip():
        return ("⚠️ 请至少填写一项实际条件或备注再保存。", "", gr.update())
    outcome = _OUTCOME_MAP.get(outcome_zh, "failed")
    strength, notes, operator = ((strength or "").strip(), (notes or "").strip(),
                                 (operator or "").strip())
    if free_record:
        ald_s, amine_s = (free_ald or "").strip(), (free_amine or "").strip()
        if not ald_s or not amine_s:
            return ("⚠️ 游离记录需填写醛和胺的 SMILES（或取消勾选并选择收藏条目）。",
                    "", gr.update())
        rec, err = _rec_create_free(ald_s, amine_s, conditions, outcome,
                                    strength, notes, operator)
    else:
        if not fav_id:
            return ("⚠️ 请先选择关联的收藏条目——或勾选「不关联收藏（游离记录）」"
                    "后直接填写醛/胺 SMILES。", "", gr.update())
        rec, err = _rec_create_linked(fav_id, conditions, outcome,
                                      strength, notes, operator)
    if err:
        return err, "", gr.update()
    recs, _ = _rec_list()
    rid = rec.get("record_id") or rec.get("id") or "?"
    return (f"✓ 实验记录已保存（{rid}）。",
            _render_records_timeline(recs or []), _record_fav_choices())


# ---------------------------------------------------------------------------
# 页⑤ 方案迭代（RAG 对接）：建议回显 + 实验记录时间线摘要
# ---------------------------------------------------------------------------

def _load_suggestions_store():
    try:
        from rag import suggestions as sug_mod
        return sug_mod, None
    except ImportError:
        return None, ("⏳ RAG 建议模块尚未上线（后端开发中）——建议 JSON 落回 "
                      "data/rag_export/suggestions/ 后在此回显。")


def _sug_list(favorite_id=None):
    """list_suggestions(favorite_id=None) -> list[dict]（签名与任务B钉死）。"""
    mod, err = _load_suggestions_store()
    if err:
        return None, err
    try:
        return mod.list_suggestions(favorite_id=favorite_id) or [], None
    except Exception as e:
        return None, f"⚠️ 建议读取失败：{_brief_error(e)}"


_SUG_TYPE_ZH = {"condition_adjust": "🔧 条件调整", "new_candidate": "🧪 新候选单体对"}
_SUG_STATUS_ZH = {
    "new": ("新建议", "#0f766e"),
    "adopted": ("已采纳", "#1d4ed8"),
    "rejected": ("已否决", "#b91c1c"),
    "done": ("已验证", "#64748b"),
}
_EV_KIND_ZH = {"experiment_record": "实验记录", "literature": "文献",
               "prediction": "预测"}


def _render_suggestion_cards(sugs: list) -> str:
    """RAG 建议卡片墙：类型 / 内容 / 依据 / 状态 / 时间 / 关联收藏。"""
    if not sugs:
        return ('<div class="placeholder-page">暂无 RAG 建议——迭代建议 JSON '
                "落回 data/rag_export/suggestions/ 后在此回显。</div>")
    cards = []
    for sug in sugs:
        stype = sug.get("type") or "?"
        type_zh = _SUG_TYPE_ZH.get(stype, _esc(stype))
        status, color = _SUG_STATUS_ZH.get(
            sug.get("status"), (sug.get("status") or "—", "#64748b"))
        created = str(sug.get("created_at") or "")[:16].replace("T", " ")
        sid = _esc(sug.get("suggestion_id") or sug.get("id") or "?")

        payload = sug.get("payload") or {}
        if stype == "condition_adjust":
            rows = ""
            for adj in payload.get("adjustments") or []:
                if isinstance(adj, dict):
                    field = _COND_LABELS.get(adj.get("field"), adj.get("field") or "?")
                    rows += (f"<li>{_esc(field)}：{_esc(adj.get('from'))} → "
                             f"<b>{_esc(adj.get('to'))}</b>"
                             + (f"<br><i>{_esc(adj['rationale'])}</i>"
                                if adj.get("rationale") else "")
                             + "</li>")
            body = f"<ul>{rows}</ul>" if rows else f"<i>{_esc(payload.get('rationale') or '')}</i>"
        elif stype == "new_candidate":
            ald = payload.get("aldehyde") or {}
            amine = payload.get("amine") or {}
            pair = (f"{_esc(ald.get('name') or ald.get('smiles') or '?')} × "
                    f"{_esc(amine.get('name') or amine.get('smiles') or '?')}")
            body = f"<div>候选组合：<b>{pair}</b></div>"
            if payload.get("rationale"):
                body += f"<i>{_esc(payload['rationale'])}</i>"
        else:
            body = f"<pre>{_esc(json.dumps(payload, ensure_ascii=False))}</pre>"

        ev_items = ""
        for ev in sug.get("evidence_refs") or []:
            if not isinstance(ev, dict):
                ev_items += f"<li>{_esc(ev)}</li>"
                continue
            kind = _EV_KIND_ZH.get(ev.get("kind"), ev.get("kind") or "依据")
            ref = str(ev.get("ref") or "")
            shown = _resolve_ref_title(ref) if ev.get("kind") == "literature" else None
            ref_txt = f"{_esc(shown)}（{_esc(ref)}）" if shown else _esc(ref)
            note = f" — {_esc(ev['note'])}" if ev.get("note") else ""
            ev_items += f"<li>{_esc(kind)}：{ref_txt}{note}</li>"
        ev_html = (f'<div class="sug-ev"><b>依据</b><ul>{ev_items}</ul></div>'
                   if ev_items else "")

        link = ""
        fid = sug.get("favorite_id")
        if fid:
            fav, _ = _fav_get(fid)
            if fav:
                _, _, ald_n, amine_n = _fav_pair(fav)
                link = f"关联收藏：{_esc(ald_n)} × {_esc(amine_n)}（{_esc(fid)}）"
            else:
                link = f"关联收藏：{_esc(fid)}"

        meta = " · ".join(x for x in (f"<code>{sid}</code>", created, link) if x)
        cards.append(
            '<div class="sug-card">'
            f'<div class="sug-head">{type_zh} '
            f'<span class="rec-outcome" style="background:{color}">{_esc(status)}</span></div>'
            f"{body}{ev_html}"
            f'<div class="sug-meta">{meta}</div>'
            "</div>")
    return '<div class="sug-wall">' + "".join(cards) + "</div>"


def _records_summary(recs: list) -> str:
    """实验记录时间线摘要一行（页⑤ 顶部）。"""
    total = len(recs)
    if not total:
        return "*暂无实验记录——在页④ 录入后此处自动汇总。*"
    counts = {}
    for r in recs:
        counts[r.get("outcome")] = counts.get(r.get("outcome"), 0) + 1
    parts = [f"{_OUTCOME_ZH[k][0]} {counts[k]}"
             for k in ("film", "partial", "failed") if counts.get(k)]
    dates = sorted((str(r.get("date")) for r in recs if r.get("date")), reverse=True)
    latest = f"，最近：{dates[0]}" if dates else ""
    return f"共 **{total}** 条实验记录（{' · '.join(parts)}{latest}）。"


def refresh_iteration_tab(fav_filter=""):
    """页⑤ 进入/刷新 → (记录摘要, 记录时间线, 建议卡片, 过滤下拉, 状态)。"""
    recs, rerr = _rec_list()
    recs = recs or []
    summary = f"⚠️ {rerr}" if rerr else _records_summary(recs)
    timeline = _render_records_timeline(recs)

    sugs, serr = _sug_list(favorite_id=(fav_filter or None))
    if serr:
        sug_html = f'<div class="placeholder-page">{_esc(serr)}</div>'
        status = serr
    else:
        sug_html = _render_suggestion_cards(sugs)
        status = f"共 {len(sugs)} 条建议。" if sugs else ""

    favs, _ = _fav_list()
    choices = [("全部", "")] + [(_fav_label(f), f.get("id")) for f in (favs or [])]
    return (summary, timeline, sug_html,
            gr.update(choices=choices, value=fav_filter or ""), status)


def refresh_suggestions(fav_filter=""):
    """仅刷新建议区（按收藏过滤下拉变更）→ (建议卡片, 状态)。"""
    sugs, serr = _sug_list(favorite_id=(fav_filter or None))
    if serr:
        return f'<div class="placeholder-page">{_esc(serr)}</div>', serr
    return (_render_suggestion_cards(sugs),
            f"共 {len(sugs)} 条建议。" if sugs else "")


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
.score-tag {
    display: inline-block; margin-top: 2px; padding: 2px 10px; border-radius: 999px;
    background: #f1f5f9; color: #475569; font-size: 0.82rem; font-weight: 500;
}
.placeholder-page { padding: 24px; text-align: center; color: #64748b; }
/* P2：收藏卡片墙 / 方案卡 / 文献标签 / 实验记录时间线 */
.fav-wall { display: grid; grid-template-columns: repeat(auto-fill, minmax(210px, 1fr)); gap: 12px; }
.fav-card { border: 1px solid #e2e8f0; border-radius: 10px; padding: 10px; background: #fff; }
.fav-card-imgs { display: flex; gap: 6px; justify-content: center; min-height: 60px; }
.fav-card-imgs img { max-width: 48%; height: 90px; object-fit: contain; }
.fav-card-title { font-weight: 600; margin: 6px 0 4px; font-size: 0.9rem; }
.fav-badge { color: #fff; border-radius: 999px; padding: 2px 10px; font-size: 0.78rem; white-space: nowrap; }
.fav-card-meta { color: #64748b; font-size: 0.75rem; margin-top: 4px; }
.fav-wall-hint { color: #64748b; font-size: 0.85rem; margin-top: 8px; }
.plan-card { border: 1px solid #99f6e4; border-left: 5px solid #0f766e; border-radius: 10px; padding: 6px 18px 14px; background: #f0fdfa; }
.plan-card h3 { margin-bottom: 4px; }
.plan-template { color: #0f766e; font-size: 0.82rem; margin-bottom: 6px; }
.plan-table { border-collapse: collapse; margin: 6px 0; }
.plan-table td { border: 1px solid #cbd5e1; padding: 4px 12px; background: #fff; }
.plan-checklist li { margin: 2px 0; }
.ref-tag { font-size: 0.7rem; background: #e2e8f0; border-radius: 999px; padding: 1px 8px; color: #475569; }
.ref-tag.auto { background: #ccfbf1; color: #0f766e; }
.rec-timeline { padding-left: 4px; }
.rec-item { border-left: 3px solid #0f766e; padding: 8px 14px; margin: 12px 0; background: #fff; border-radius: 6px; box-shadow: 0 1px 2px rgba(15,23,42,.06); }
.rec-head { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.rec-date { color: #64748b; font-size: 0.82rem; }
.rec-outcome { color: #fff; border-radius: 999px; padding: 1px 10px; font-size: 0.78rem; }
.rec-compare { margin: 6px 0; font-size: 0.92rem; }
.rec-conds { color: #334155; font-size: 0.85rem; margin: 4px 0; }
.rec-meta { color: #64748b; font-size: 0.8rem; }
.rec-notes { color: #475569; font-size: 0.85rem; margin-top: 4px; }
/* P3：RAG 建议卡片 / 文献 paper_id 小字 */
.sug-wall { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 12px; }
.sug-card { border: 1px solid #e2e8f0; border-left: 4px solid #0f766e; border-radius: 10px; padding: 10px 14px; background: #fff; }
.sug-head { font-weight: 600; display: flex; gap: 8px; align-items: center; margin-bottom: 4px; }
.sug-ev { font-size: 0.85rem; color: #334155; margin-top: 6px; }
.sug-meta { color: #64748b; font-size: 0.78rem; margin-top: 6px; }
.ref-pid { color: #94a3b8; font-size: 0.75rem; }
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
                        with gr.Group():
                            gr.Markdown("#### 收藏与方案卡")
                            fav_notes_input1 = gr.Textbox(
                                label="收藏备注（可选）", lines=1,
                                placeholder="例如：G2 候选，优先验证")
                            with gr.Row():
                                favorite_btn = gr.Button("☆ 收藏这组单体", size="sm")
                                plan_btn1 = gr.Button("生成实验方案卡", size="sm")
                            fav_status1 = gr.Markdown()

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
                        plan_html1 = gr.HTML(
                            value="<i>点击左侧「生成实验方案卡」后在此显示："
                                  "条件表 + 加料顺序 + ⛔ 防错清单 + 单体提示。</i>")

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
                favorite_btn.click(fn=favorite_current,
                                   inputs=[ald_input, amine_input, fav_notes_input1],
                                   outputs=[fav_status1])
                plan_btn1.click(fn=plan_card_for_input,
                                inputs=[ald_input, amine_input],
                                outputs=[plan_html1, fav_status1])

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
                gr.Markdown(
                    "*分数口径：排序分数 = 树模型与 GNN 两模型较高值（乐观召回口径），"
                    "高分请结合 OOD 与不确定度判断；⛔ OOD 不适用不出分。*")
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

            # ===================== 页③ 收藏夹 =====================
            with gr.Tab("③ 收藏夹") as fav_tab:
                with gr.Row():
                    fav_refresh_btn = gr.Button("刷新收藏夹", size="sm", scale=1)
                    fav_status = gr.Markdown(value="*进入本页自动加载。*", container=False)
                fav_cards = gr.HTML()
                with gr.Group():
                    gr.Markdown("#### 收藏详情")
                    # allow_custom_value=True 必须保留：choices 由 refresh 动态
                    # 下发，服务端组件 choices 恒为 []，gradio 6 的 preprocess 会
                    # 拿服务端 choices 校验浏览器选中值，校验不过事件直接报错、
                    # 详情不刷新（Bug：收藏夹点选后不显示详情的根因）。
                    fav_select = gr.Dropdown(label="选择收藏条目", choices=[],
                                             interactive=True,
                                             allow_custom_value=True)
                    fav_detail_info = gr.Markdown()
                    fav_detail_snapshot = gr.Markdown()
                    with gr.Row():
                        fav_rescore_btn = gr.Button("重新打分", size="sm")
                        fav_plan_btn = gr.Button("生成方案卡", size="sm")
                        fav_delete_btn = gr.Button("删除收藏", size="sm", variant="stop")
                    fav_action_status = gr.Markdown()
                    fav_plan_html = gr.HTML()
                with gr.Group():
                    gr.Markdown("#### 备注")
                    fav_notes_input = gr.Textbox(label="历史备注", lines=2)
                    fav_notes_save_btn = gr.Button("保存备注", size="sm")
                    fav_notes_status = gr.Markdown()
                with gr.Group():
                    gr.Markdown("#### 文献")
                    fav_refs_html = gr.HTML(
                        value="<i>选择收藏条目后显示文献列表。</i>")
                    fav_auto_ref_btn = gr.Button("自动匹配相关文献", size="sm")
                    with gr.Row():
                        ref_title = gr.Textbox(label="标题", scale=3)
                        ref_doi = gr.Textbox(label="DOI", scale=2)
                    with gr.Row():
                        ref_url = gr.Textbox(label="链接或本地路径", scale=3)
                        ref_note = gr.Textbox(label="备注（支撑哪条决策）", scale=2)
                    ref_add_btn = gr.Button("手动添加文献", size="sm")
                    ref_status = gr.Markdown()
                with gr.Group():
                    gr.Markdown("#### 关联实验记录")
                    fav_records_html = gr.HTML(
                        value="<i>选择收藏条目后显示关联实验记录。</i>")

                _detail_outputs = [fav_detail_info, fav_detail_snapshot,
                                   fav_notes_input, fav_refs_html, fav_records_html]
                fav_tab.select(fn=refresh_favorites,
                               outputs=[fav_cards, fav_select, fav_status])
                fav_refresh_btn.click(fn=refresh_favorites,
                                      outputs=[fav_cards, fav_select, fav_status])
                fav_select.change(fn=show_favorite_detail, inputs=[fav_select],
                                  outputs=_detail_outputs)
                fav_notes_save_btn.click(fn=save_favorite_notes,
                                         inputs=[fav_select, fav_notes_input],
                                         outputs=[fav_notes_status])
                ref_add_btn.click(fn=add_favorite_reference,
                                  inputs=[fav_select, ref_title, ref_doi,
                                          ref_url, ref_note],
                                  outputs=[ref_status, fav_refs_html])
                fav_auto_ref_btn.click(fn=auto_match_favorite_refs,
                                       inputs=[fav_select],
                                       outputs=[fav_refs_html, ref_status])
                fav_rescore_btn.click(fn=rescore_favorite, inputs=[fav_select],
                                      outputs=[fav_action_status,
                                               fav_detail_snapshot])
                fav_plan_btn.click(fn=plan_card_for_favorite, inputs=[fav_select],
                                   outputs=[fav_plan_html, fav_action_status])
                fav_delete_btn.click(fn=delete_favorite, inputs=[fav_select],
                                     outputs=[fav_action_status, fav_cards,
                                              fav_select] + _detail_outputs)

            # ===================== 页④ 实验记录 =====================
            with gr.Tab("④ 实验记录") as rec_tab:
                with gr.Group():
                    gr.Markdown("#### 录入实验记录")
                    # allow_custom_value：同页③，避免动态 choices 被服务端校验拦截
                    rec_fav_select = gr.Dropdown(
                        label="关联收藏条目（记录挂在收藏上，"
                              "单体与预测快照自动带入）",
                        choices=[], interactive=True, allow_custom_value=True)
                    rec_free = gr.Checkbox(
                        label="不关联收藏（游离记录）——直接填写醛/胺 SMILES",
                        value=False)
                    with gr.Row(visible=False) as rec_free_row:
                        rec_free_ald = gr.Textbox(
                            label="醛单体 SMILES", placeholder="游离记录必填")
                        rec_free_amine = gr.Textbox(
                            label="胺单体 SMILES", placeholder="游离记录必填")
                    with gr.Row():
                        rec_solvent = gr.Textbox(label="溶剂",
                                                 placeholder="甲苯 / BTF/二氧六环")
                        rec_modulator = gr.Textbox(label="调制剂",
                                                   placeholder="苯胺 13.7 μL")
                        rec_catalyst = gr.Textbox(label="催化剂",
                                                  placeholder="6M 乙酸 0.2 mL")
                    with gr.Row():
                        rec_temp = gr.Textbox(label="温度 (°C)", placeholder="120")
                        rec_time = gr.Textbox(label="时间 (天)", placeholder="3")
                        rec_order = gr.Textbox(
                            label="加料顺序", placeholder="先醛+苯胺，后胺，最后乙酸")
                    with gr.Row():
                        rec_outcome = gr.Radio(
                            ["成膜", "部分成膜", "失败"], value="成膜",
                            label="实验结果", scale=1)
                        rec_strength = gr.Textbox(
                            label="机械强度/膜质量描述", scale=2,
                            placeholder="例如：膜完整可剥离，弯折不裂")
                    with gr.Row():
                        rec_operator = gr.Textbox(label="操作人", scale=1)
                        rec_notes = gr.Textbox(label="备注", scale=2)
                    rec_submit_btn = gr.Button("保存实验记录", variant="primary")
                    rec_status = gr.Markdown()
                with gr.Group():
                    gr.Markdown("#### 记录列表（当初预测 vs 实际结果）")
                    rec_refresh_btn = gr.Button("刷新记录", size="sm")
                    rec_list_html = gr.HTML()

                rec_tab.select(fn=refresh_records_tab,
                               outputs=[rec_fav_select, rec_list_html])
                rec_refresh_btn.click(fn=refresh_records_tab,
                                      outputs=[rec_fav_select, rec_list_html])
                rec_free.change(fn=lambda v: gr.update(visible=bool(v)),
                                inputs=[rec_free], outputs=[rec_free_row])
                rec_submit_btn.click(
                    fn=submit_record,
                    inputs=[rec_fav_select, rec_solvent, rec_modulator,
                            rec_catalyst, rec_temp, rec_time, rec_order,
                            rec_outcome, rec_strength, rec_operator, rec_notes,
                            rec_free, rec_free_ald, rec_free_amine],
                    outputs=[rec_status, rec_list_html, rec_fav_select])

            # ===================== 页⑤ 方案迭代（RAG 对接） =====================
            with gr.Tab("⑤ 方案迭代") as iter_tab:
                gr.Markdown(
                    "实验记录沉淀 → RAG 迭代 → 建议回显（文件对接契约见 "
                    "`data/rag_export/README.md`；建议由 minimax RAG 写入 "
                    "`suggestions/`，本页只读展示并与收藏条目关联）。")
                with gr.Group():
                    gr.Markdown("#### 实验记录时间线摘要")
                    iter_rec_summary = gr.Markdown()
                    iter_rec_html = gr.HTML()
                with gr.Group():
                    gr.Markdown("#### RAG 迭代建议")
                    with gr.Row():
                        iter_refresh_btn = gr.Button("刷新", size="sm", scale=1)
                        iter_fav_filter = gr.Dropdown(
                            label="按收藏过滤", choices=[("全部", "")], value="",
                            interactive=True, allow_custom_value=True, scale=3)
                    iter_sug_html = gr.HTML()
                    iter_status = gr.Markdown()

                _iter_outputs = [iter_rec_summary, iter_rec_html, iter_sug_html,
                                 iter_fav_filter, iter_status]
                iter_tab.select(fn=refresh_iteration_tab, inputs=[iter_fav_filter],
                                outputs=_iter_outputs)
                iter_refresh_btn.click(fn=refresh_iteration_tab,
                                       inputs=[iter_fav_filter],
                                       outputs=_iter_outputs)
                iter_fav_filter.change(fn=refresh_suggestions,
                                       inputs=[iter_fav_filter],
                                       outputs=[iter_sug_html, iter_status])

    # Gradio 6 把 theme/css 从 Blocks 构造器移到 launch()；构造器 kwargs 仅存入
    # _deprecated_theme/_deprecated_css 并告警。这里直接设置这两个内部字段，
    # 效果与构造器传入完全一致（launch 会回退读取），且无论谁启动 app 主题都生效。
    app._deprecated_theme = _build_theme()
    app._deprecated_css = CUSTOM_CSS
    return app


if __name__ == "__main__":
    app = create_app()
    app.launch(server_name="127.0.0.1", server_port=7860, share=False)
