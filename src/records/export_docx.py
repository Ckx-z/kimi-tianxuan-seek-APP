"""实验记录 Word（.docx）导出。

build_record_docx(rec, version="") -> bytes：完整实验记录报告；
export_filename(rec) -> str：中文文件名（路由层走 RFC 5987 filename*）。

排版约定（与项目既有 docx 经验一致）：
- python-docx 默认样式，标题层级 0/1 清晰；
- 表格一律 'Table Grid'（带边框）；
- 中文字体宋体：Normal/标题样式 eastAsia + 正文 run 显式设置双保险。

降级路径（不抛异常，保证任何记录都能导出）：
- 无关联收藏 / 收藏被删 / 无打分快照 → 模型打分小节写「未打分」；
- LLM 未配置 / 性质卡生成失败 / RDKit 不可用 → 单体性质小节保留占位，
  写「（未配置 LLM，本节略）」。
"""

from __future__ import annotations

import io
import logging
import re
from datetime import datetime

from docx import Document
from docx.oxml.ns import qn

logger = logging.getLogger(__name__)

# 与前端 components/records/meta.ts CONDITION_LABELS 一致的九键中文名
_CONDITION_LABELS = {
    "solvent_1": "溶剂一",
    "solvent_2": "溶剂二",
    "eluent": "洗脱剂",
    "modulator": "调制剂",
    "catalyst": "催化剂",
    "temperature_c": "温度（℃）",
    "time_days": "时间（天）",
    "vessel": "容器",
    "addition_order": "加料顺序",
}

_OUTCOME_LABELS = {
    "film": "成膜",
    "partial": "部分成膜",
    "failed": "失败",
    "": "未定",
}

_STATUS_LABELS = {"draft": "草稿", "final": "正式"}

_OOD_LABELS = {
    "none": "适用域内（无警告）",
    "warning": "警告：接近模型适用域边界，打分可信度降低",
    "out": "超出适用域（模型不适用）",
}

# RDKit 事实键中文名（src/recommend/monomer_props.py _FACT_KEYS）
_FACT_LABELS = {
    "mw": "分子量",
    "xlogp": "XlogP",
    "tpsa": "TPSA",
    "hbd": "氢键给体数",
    "hba": "氢键受体数",
    "aromatic_rings": "芳香环数",
    "f_count": "氟原子数",
    "rotatable_bonds": "可旋转键数",
}

_CN_FONT = "宋体"


# ---------------------------------------------------------------------------
# 字体 / 段落工具
# ---------------------------------------------------------------------------


def _set_style_cn_fonts(doc: Document) -> None:
    """Normal 与标题样式设置宋体（ascii 名 + eastAsia），表格单元格继承生效。"""
    for name in ("Normal", "Title", "Heading 1", "Heading 2", "Heading 3",
                 "Heading 4"):
        try:
            style = doc.styles[name]
        except KeyError:
            continue
        style.font.name = _CN_FONT
        rpr = style.element.get_or_add_rPr()
        rpr.get_or_add_rFonts().set(qn("w:eastAsia"), _CN_FONT)


def _cn_run(run) -> None:
    """run 级中文字体双保险（正文段落显式指定宋体 + eastAsia）。"""
    run.font.name = _CN_FONT
    rpr = run._element.get_or_add_rPr()
    rpr.get_or_add_rFonts().set(qn("w:eastAsia"), _CN_FONT)


def _add_text(doc: Document, text: str) -> None:
    """多行纯文本 → 每行一个段落（python-docx 不渲染 run 内 \\n）。"""
    lines = str(text).splitlines() or [""]
    for line in lines:
        p = doc.add_paragraph()
        _cn_run(p.add_run(line))


def _kv_table(doc: Document, rows: list[tuple[str, str]]) -> None:
    """两列「字段 | 值」Table Grid 表。"""
    table = doc.add_table(rows=len(rows), cols=2)
    table.style = "Table Grid"
    for i, (k, v) in enumerate(rows):
        table.rows[i].cells[0].text = str(k)
        table.rows[i].cells[1].text = str(v)


# ---------------------------------------------------------------------------
# 数据装配（均为只读 + 容错）
# ---------------------------------------------------------------------------


def _prediction_snapshot(rec: dict) -> dict | None:
    """模型打分快照：优先取关联收藏的 latest_prediction（最新），
    收藏缺失/删除时回落到记录创建时冗余的 prediction_snapshot。
    """
    snap = None
    fav_id = rec.get("favorite_id")
    if fav_id:
        try:
            from favorites import store as favorites_store

            fav = favorites_store.get_favorite(fav_id)
            if isinstance(fav, dict):
                cand = fav.get("latest_prediction")
                if isinstance(cand, dict) and cand.get("score") is not None:
                    snap = cand
        except Exception as exc:
            logger.warning("导出时读取收藏 %s 打分快照失败: %s", fav_id, exc)
    if snap is None:
        cand = rec.get("prediction_snapshot")
        if isinstance(cand, dict) and cand.get("score") is not None:
            snap = cand
    return snap


def _monomer_props_section(doc: Document, label: str, monomer: dict,
                           level: int = 2) -> None:
    """单体性质小节：RDKit 事实行 + LLM 中文解读；任何失败降级为占位说明。"""
    monomer = monomer if isinstance(monomer, dict) else {}
    smiles = str(monomer.get("smiles") or "").strip()
    name = str(monomer.get("name") or "").strip()
    doc.add_heading(f"{label}性质解读", level=level)
    if not smiles:
        doc.add_paragraph("（未填写 SMILES，本节略）")
        return
    try:
        from recommend.monomer_props import get_monomer_properties

        card = get_monomer_properties(smiles, name=name)
    except Exception as exc:
        logger.warning("导出时性质卡生成失败（%s）: %s", smiles, exc)
        card = None
    if not isinstance(card, dict):
        doc.add_paragraph("（未配置 LLM，本节略）")
        return
    facts = card.get("facts") or {}
    if facts:
        facts_text = "；".join(
            f"{_FACT_LABELS.get(k, k)}：{facts[k]}" for k in _FACT_LABELS if k in facts
        )
        doc.add_paragraph(f"RDKit 计算事实：{facts_text}")
    narrative = card.get("narrative")
    if isinstance(narrative, str) and narrative.strip():
        _add_text(doc, narrative.strip())
    else:
        doc.add_paragraph("（未配置 LLM，本节略）")


def _sorted_timeline(timeline: list) -> list[dict]:
    """时间线条目按时间排序：time_label 可解析为日期时间的按时间升序，
    不可解析的保持原顺序排在其后（time_label 为自由文本，如「第 2 天」）。
    """
    def _key(item: tuple[int, dict]):
        idx, entry = item
        label = str(entry.get("time_label") or "").strip()
        try:
            return (0, datetime.fromisoformat(label).timestamp(), idx)
        except ValueError:
            return (1, 0.0, idx)

    entries = [e for e in (timeline or []) if isinstance(e, dict)]
    return [e for _, e in sorted(enumerate(entries), key=_key)]


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def build_record_docx(rec: dict, version: str = "") -> bytes:
    """生成单条实验记录 Word 报告，返回 docx 字节串。"""
    doc = Document()
    _set_style_cn_fonts(doc)

    doc.add_heading("实验记录", 0)
    _build_record_body(doc, rec, base_level=1)

    # 页脚：导出方 + 版本号
    footer_text = "由 COF 科研助手导出"
    if version:
        footer_text += f" · 版本 v{version}"
    for section in doc.sections:
        p = section.footer.paragraphs[0] if section.footer.paragraphs else section.footer.add_paragraph()
        _cn_run(p.add_run(footer_text))

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _build_record_body(doc: Document, rec: dict, base_level: int = 1) -> None:
    """向 doc 追加一条实验记录的完整正文（不含标题与页脚）。

    单条导出与汇总导出共用此函数；base_level 为各小节标题层级
    （单条导出用 1，汇总导出在组标题/记录标题之下用 3）。
    """
    rec = rec if isinstance(rec, dict) else {}
    record_id = str(rec.get("record_id") or "")
    experiment_no = str(rec.get("experiment_no") or "").strip()
    status = _STATUS_LABELS.get(str(rec.get("status") or ""), str(rec.get("status") or ""))
    today = datetime.now().astimezone().date().isoformat()

    # 基本信息
    doc.add_heading("基本信息", level=base_level)
    _kv_table(doc, [
        ("实验编号", experiment_no or "（未填写）"),
        ("记录 ID", record_id),
        ("状态", status or "—"),
        ("记录日期", str(rec.get("date") or "—")),
        ("实验结果", _OUTCOME_LABELS.get(str(rec.get("outcome") or ""), "未定")),
        ("机械强度", str(rec.get("strength") or "—")),
        ("操作人", str(rec.get("operator") or "—")),
        ("导出日期", today),
    ])
    notes = str(rec.get("notes") or "").strip()
    if notes:
        p = doc.add_paragraph()
        _cn_run(p.add_run(f"备注：{notes}"))

    # 单体信息表
    doc.add_heading("单体信息", level=base_level)
    table = doc.add_table(rows=4, cols=3)
    table.style = "Table Grid"
    for j, head in enumerate(("单体", "醛单体", "胺单体")):
        table.rows[0].cells[j].text = head
    ald = rec.get("aldehyde") if isinstance(rec.get("aldehyde"), dict) else {}
    amine = rec.get("amine") if isinstance(rec.get("amine"), dict) else {}
    for i, field in enumerate(("name", "smiles", "cas"), start=1):
        label = {"name": "名称", "smiles": "SMILES", "cas": "CAS"}[field]
        table.rows[i].cells[0].text = label
        table.rows[i].cells[1].text = str(ald.get(field) or "—")
        table.rows[i].cells[2].text = str(amine.get(field) or "—")

    # 模型打分（关联收藏 latest_prediction / 记录快照 / 未打分）
    doc.add_heading("模型打分", level=base_level)
    snap = _prediction_snapshot(rec)
    if snap is None:
        doc.add_paragraph("未打分（无关联收藏的打分快照）。")
    else:
        rows = [("综合评分（成膜倾向）", f"{float(snap['score']):.3f}")]
        if snap.get("std") is not None:
            rows.append(("不确定度（±std）", f"± {float(snap['std']):.3f}"))
        ood = str(snap.get("ood") or "")
        rows.append(("OOD 等级", _OOD_LABELS.get(ood, ood or "—")))
        if snap.get("tree_score") is not None:
            rows.append(("树模型分量", f"{float(snap['tree_score']):.3f}"))
        if snap.get("gnn_score") is not None:
            rows.append(("GNN 分量", f"{float(snap['gnn_score']):.3f}"))
        if snap.get("score_policy"):
            rows.append(("打分口径", str(snap["score_policy"])))
        if snap.get("arm"):
            rows.append(("打分臂", str(snap["arm"])))
        if snap.get("date"):
            rows.append(("打分时间", str(snap["date"])))
        _kv_table(doc, rows)

    # 单体性质（LLM 解读；未配置/失败保留小节占位）
    doc.add_heading("单体性质", level=base_level)
    _monomer_props_section(doc, "醛单体", ald, level=base_level + 1)
    _monomer_props_section(doc, "胺单体", amine, level=base_level + 1)

    # 实验条件（所有已填字段；九键用中文名，额外键原样保留）
    doc.add_heading("实验条件", level=base_level)
    cond = rec.get("conditions") if isinstance(rec.get("conditions"), dict) else {}
    filled = [
        (_CONDITION_LABELS.get(str(k), str(k)), str(v))
        for k, v in cond.items()
        if v not in (None, "")
    ]
    if filled:
        _kv_table(doc, filled)
    else:
        doc.add_paragraph("（未填写实验条件）")

    # 完整实验流程（全文）
    doc.add_heading("完整实验流程", level=base_level)
    process_notes = str(rec.get("process_notes") or "").strip()
    if process_notes:
        _add_text(doc, process_notes)
    else:
        doc.add_paragraph("（未填写）")

    # 时间线（按时间排序：时间 | 过程记录 | 附件名）
    doc.add_heading("时间线", level=base_level)
    timeline = _sorted_timeline(rec.get("timeline") or [])
    if timeline:
        table = doc.add_table(rows=len(timeline) + 1, cols=3)
        table.style = "Table Grid"
        for j, head in enumerate(("时间", "过程记录", "附件名")):
            table.rows[0].cells[j].text = head
        for i, entry in enumerate(timeline, start=1):
            atts = [
                str(m.get("filename") or m.get("attachment_id") or "")
                for m in entry.get("attachments") or []
                if isinstance(m, dict)
            ]
            table.rows[i].cells[0].text = str(entry.get("time_label") or "—")
            table.rows[i].cells[1].text = str(entry.get("description") or "—")
            table.rows[i].cells[2].text = "；".join(a for a in atts if a) or "—"
    else:
        doc.add_paragraph("（无时间点记录）")

    # 自我总结 / 我认为的失误（有则）
    self_summary = str(rec.get("self_summary") or "").strip()
    if self_summary:
        doc.add_heading("自我总结", level=base_level)
        _add_text(doc, self_summary)
    mistakes = str(rec.get("mistakes") or "").strip()
    if mistakes:
        doc.add_heading("我认为的失误", level=base_level)
        _add_text(doc, mistakes)


def _group_title(fav: dict) -> str:
    """汇总导出的组标题：单体组名称（醛 + 胺），名称为空回落到 SMILES。"""
    fav = fav if isinstance(fav, dict) else {}
    ald = fav.get("aldehyde") if isinstance(fav.get("aldehyde"), dict) else {}
    amine = fav.get("amine") if isinstance(fav.get("amine"), dict) else {}
    ald_name = str(ald.get("name") or "").strip() or str(ald.get("smiles") or "未知醛单体")
    amine_name = str(amine.get("name") or "").strip() or str(amine.get("smiles") or "未知胺单体")
    return f"{ald_name} + {amine_name}"


def build_bundle_docx(groups: list[dict], version: str = "") -> bytes:
    """按收藏分组导出多组实验记录为一份 Word，返回 docx 字节串。

    groups: [{"favorite": fav_dict, "records": [rec_dict, ...]}, ...]
    （records 由调用方按时间序提供）。封面含标题/导出时间/软件版本；
    每组一级标题为单体组名称（醛+胺），组内每条记录二级标题后接与单条
    导出一致的正文；收藏无记录时标注「暂无实验记录」。
    """
    doc = Document()
    _set_style_cn_fonts(doc)
    now = datetime.now().astimezone()

    doc.add_heading("实验记录汇总导出", 0)
    p = doc.add_paragraph()
    _cn_run(p.add_run(f"导出时间：{now.isoformat(timespec='seconds')}"))
    if version:
        p = doc.add_paragraph()
        _cn_run(p.add_run(f"软件版本：v{version}"))
    p = doc.add_paragraph()
    _cn_run(p.add_run(f"收藏分组数：{len(groups)}"))

    for group in groups:
        group = group if isinstance(group, dict) else {}
        fav = group.get("favorite") if isinstance(group.get("favorite"), dict) else {}
        records = [r for r in (group.get("records") or []) if isinstance(r, dict)]
        doc.add_heading(_group_title(fav), level=1)
        if not records:
            doc.add_paragraph("暂无实验记录")
            continue
        for rec in records:
            no = str(rec.get("experiment_no") or "").strip()
            rec_id = str(rec.get("record_id") or "")
            label = f"实验记录 {no}" if no else f"实验记录 {rec_id}"
            doc.add_heading(label, level=2)
            _build_record_body(doc, rec, base_level=3)

    # 页脚：导出方 + 版本号
    footer_text = "由 COF 科研助手导出"
    if version:
        footer_text += f" · 版本 v{version}"
    for section in doc.sections:
        p = section.footer.paragraphs[0] if section.footer.paragraphs else section.footer.add_paragraph()
        _cn_run(p.add_run(footer_text))

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def bundle_export_filename(now: datetime | None = None) -> str:
    """汇总导出文件名：实验记录汇总_YYYYMMDD_HHMM.docx。"""
    now = now or datetime.now()
    return f"实验记录汇总_{now.strftime('%Y%m%d_%H%M')}.docx"


_FILENAME_BAD_CHARS = re.compile(r'[\\/:*?"<>|\r\n]+')


def export_filename(rec: dict) -> str:
    """中文下载文件名：实验记录_<编号>_<记录ID>.docx（编号含非法字符时清洗）。"""
    rec = rec if isinstance(rec, dict) else {}
    record_id = str(rec.get("record_id") or "record")
    no = _FILENAME_BAD_CHARS.sub("_", str(rec.get("experiment_no") or "").strip())
    stem = f"实验记录_{no}_{record_id}" if no else f"实验记录_{record_id}"
    return f"{stem}.docx"
