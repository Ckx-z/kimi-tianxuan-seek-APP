"""GNN 成膜打分修正机制路由（v1.8.0，需求一）。

反馈三通道（打分纠错 / 文献 PDF 半自动提取 / 实验 CSV）+ 校验确认 +
重训 job（微调/闸门/版本 registry）+ 版本切换与对比。训练环境
（dphuanjing）缺失时重训入口返回明确错误，前端据此置灰。

文献 PDF 提取（import-pdf）：PyMuPDF 全文 → LLM 结构化提取
（未配置时跳过）→ RDKit 正则兜底扫描 SMILES → 按醛/胺角色配对，
返回候选预览（不落库）；用户确认后 confirm-batch 入库。
"""

from __future__ import annotations

import csv
import io
import json
import re

from fastapi import APIRouter, File, HTTPException, UploadFile

from ..schemas import (GnnFeedbackBatchConfirm, GnnFeedbackCreate,
                       GnnFeedbackUpdate, GnnRetrainRequest)

router = APIRouter(prefix="/api/gnn", tags=["gnn-feedback"])

_SMILES_TOKEN = re.compile(r"[A-Za-z0-9@+\-\[\]()\\/#%=.]{6,}")

MAX_PDF_BYTES = 20 * 1024 * 1024
MAX_PAIRS_PREVIEW = 30


def _fb():
    from src.predictor import gnn_feedback
    return gnn_feedback


def _jobs():
    from src.predictor import gnn_jobs
    return gnn_jobs


def _role(smiles: str) -> str | None:
    """按官能团判定单体角色：aldehyde / amine / None。"""
    from rdkit import Chem
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None
    n_ald = len(mol.GetSubstructMatches(Chem.MolFromSmarts("[CX3H](=O)")))
    n_amine = (len(mol.GetSubstructMatches(
                   Chem.MolFromSmarts("[NX3H2;!$(N[C,S]=O);!$(NO);!$(N=O)]")))
               + len(mol.GetSubstructMatches(
                   Chem.MolFromSmarts("[NX3H1;!$(N[C,S]=O);!$(NO);!$(N=O)]([#6])[#6]"))))
    if n_ald > 0 and n_amine == 0:
        return "aldehyde"
    if n_amine > 0 and n_ald == 0:
        return "amine"
    return None


def _scan_smiles_candidates(text: str) -> list[dict]:
    """RDKit 正则兜底：扫出 SMILES 并按角色归类。

    令牌可能带外侧括号/标点（SMILES 内部才合法），逐变体尝试解析。
    """
    from rdkit import Chem
    alds, amines, seen = [], [], set()
    for m in _SMILES_TOKEN.finditer(text or ""):
        raw = m.group(0)
        if raw in seen:
            continue
        seen.add(raw)
        mol = None
        for cand in (raw, raw.strip("()[]"), raw.rstrip(".,;:)"),
                     raw.strip("()[]").rstrip(".,;:)")):
            if len(cand) > 400:
                continue
            mol = Chem.MolFromSmiles(cand)
            if mol is not None:
                break
        if mol is None:
            continue
        s = Chem.MolToSmiles(mol)
        role = _role(s)
        if role == "aldehyde":
            alds.append(s)
        elif role == "amine":
            amines.append(s)
    pairs = []
    for a in alds[:8]:
        for b in amines[:8]:
            pairs.append({"aldehyde_smiles": a, "amine_smiles": b,
                          "label": None, "evidence": "SMILES 扫描（角色自动判定）"})
            if len(pairs) >= MAX_PAIRS_PREVIEW:
                return pairs
    return pairs


def _llm_extract_pairs(text: str) -> list[dict] | None:
    """LLM 结构化提取（未配置/失败返回 None）。"""
    try:
        from src.assistant import llm_bridge
        if not llm_bridge.is_configured():
            return None
        prompt = (
            "从下面的 COF 文献片段中提取「醛单体 + 胺单体」缩聚成膜体系。"
            "只输出一个 JSON 数组（不要 markdown 围栏），每项："
            '{"aldehyde_smiles": "...", "amine_smiles": "...", '
            '"label": 1|0.5|0, "evidence": "一句话依据（含成膜/不成膜现象或表征）"}。'
            "仅提取文中明确出现的体系；没有把握的不要写。\n\n文献片段：\n"
            + text[:12000])
        raw = llm_bridge.chat_text(
            [{"role": "user", "content": prompt}], max_tokens=4000)
        if not raw:
            return None
        m = re.search(r"\[[\s\S]*\]", raw.strip())
        arr = json.loads(m.group(0)) if m else None
        if not isinstance(arr, list):
            return None
        out = []
        from rdkit import Chem
        for item in arr:
            if not isinstance(item, dict):
                continue
            a = str(item.get("aldehyde_smiles") or "").strip()
            b = str(item.get("amine_smiles") or "").strip()
            if not a or not b or Chem.MolFromSmiles(a) is None \
                    or Chem.MolFromSmiles(b) is None:
                continue
            try:
                label = float(item.get("label"))
                label = label if label in (0.0, 0.5, 1.0) else None
            except (TypeError, ValueError):
                label = None
            out.append({"aldehyde_smiles": a, "amine_smiles": b,
                        "label": label,
                        "evidence": str(item.get("evidence") or "")[:200]})
            if len(out) >= MAX_PAIRS_PREVIEW:
                break
        return out or None
    except Exception:
        return None


# ---------------------------------------------------------------- 反馈 CRUD

@router.post("/feedback", status_code=201)
def submit_feedback(req: GnnFeedbackCreate):
    """打分纠错反馈（前端「反馈打分不合理」）。"""
    fb = _fb()
    try:
        return fb.submit(req.ald_smiles, req.amine_smiles, req.label,
                         note=req.note, source=req.source)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/feedback")
def list_feedback(status: str | None = None):
    fb = _fb()
    rows = fb.list_feedback(status)
    return {"feedback": rows, "count": len(rows),
            "confirmed": len(fb.confirmed_rows())}


@router.patch("/feedback/{feedback_id}")
def update_feedback(feedback_id: str, req: GnnFeedbackUpdate):
    fb = _fb()
    if req.label is None and req.note is None:
        raise HTTPException(400, "至少提供 label 或 note 一项")
    try:
        rec = fb.update_feedback(feedback_id, label=req.label, note=req.note)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if rec is None:
        raise HTTPException(404, f"反馈不存在或状态不可改: {feedback_id}")
    return rec


@router.delete("/feedback/{feedback_id}")
def delete_feedback(feedback_id: str):
    fb = _fb()
    if not fb.delete_feedback(feedback_id):
        raise HTTPException(404, f"反馈不存在: {feedback_id}")
    return {"deleted": True, "feedback_id": feedback_id}


@router.post("/feedback/{feedback_id}/confirm")
def confirm_feedback(feedback_id: str):
    """确认反馈（can_network/去重/冲突校验 → confirmed/conflict）。"""
    fb = _fb()
    rec = fb.confirm(feedback_id)
    if rec is None:
        raise HTTPException(404, f"反馈不存在: {feedback_id}")
    return rec


@router.post("/feedback/{feedback_id}/reject")
def reject_feedback(feedback_id: str):
    fb = _fb()
    rec = fb.reject(feedback_id)
    if rec is None:
        raise HTTPException(404, f"反馈不存在: {feedback_id}")
    return rec


@router.post("/feedback/confirm-batch")
def confirm_feedback_batch(req: GnnFeedbackBatchConfirm):
    fb = _fb()
    out = []
    for fid in req.feedback_ids:
        rec = fb.confirm(fid)
        if rec is not None:
            out.append(rec)
    return {"confirmed": out, "count": len(out)}


# ---------------------------------------------------------------- 导入

@router.post("/feedback/import-table")
async def import_feedback_table(file: UploadFile = File(...)):
    """实验反馈 CSV/Excel 导入（列 aldehyde_smiles, amine_smiles, label[, note]）。

    逐行校验后批量入 pending；非法行计数返回。
    """
    fb = _fb()
    filename = (file.filename or "").strip()
    data = await file.read()
    if filename.lower().endswith((".xlsx", ".xls")):
        try:
            import openpyxl  # noqa: F401
        except ImportError:
            raise HTTPException(400, "服务端未安装 openpyxl，暂不支持 Excel；请导出 CSV")
        raise HTTPException(400, "Excel 解析暂未启用，请导出 CSV 后上传")
    if not filename.lower().endswith(".csv"):
        raise HTTPException(400, "请上传 CSV 文件（列：aldehyde_smiles, amine_smiles, label[, note]）")
    try:
        rows = list(csv.DictReader(io.StringIO(
            data.decode("utf-8-sig", errors="replace"))))
    except Exception as exc:
        raise HTTPException(400, f"CSV 解析失败：{exc}")
    created, failed = [], []
    for i, r in enumerate(rows, 2):
        try:
            rec = fb.submit(
                str(r.get("aldehyde_smiles") or "").strip(),
                str(r.get("amine_smiles") or "").strip(),
                float(r.get("label") or ""),
                note=str(r.get("note") or "").strip(),
                source="experiment_csv")
            created.append(rec)
        except (ValueError, TypeError) as exc:
            failed.append({"row": i, "reason": str(exc)})
    return {"created": len(created), "failed": failed,
            "feedback": created}


@router.post("/feedback/import-pdf")
async def import_feedback_pdf(file: UploadFile = File(...)):
    """文献 PDF 半自动提取（LLM 结构化 + RDKit 正则兜底）→ 候选预览。"""
    filename = (file.filename or "").strip()
    if filename and not filename.lower().endswith(".pdf"):
        raise HTTPException(400, "请上传 PDF 文件")
    data = await file.read()
    if not data:
        raise HTTPException(400, "上传文件为空")
    if len(data) > MAX_PDF_BYTES:
        raise HTTPException(413, f"PDF 超过 {MAX_PDF_BYTES // (1024 * 1024)}MB 上限")
    try:
        import fitz
        doc = fitz.open(stream=data, filetype="pdf")
        text = "\n".join(page.get_text() for page in doc)
        doc.close()
    except Exception as exc:
        raise HTTPException(400, f"PDF 解析失败：{exc}")
    llm_pairs = _llm_extract_pairs(text)
    candidates = llm_pairs if llm_pairs else _scan_smiles_candidates(text)
    return {
        "filename": filename,
        "text_len": len(text),
        "llm_used": bool(llm_pairs),
        "candidates": candidates,
        "candidate_count": len(candidates),
    }


# ---------------------------------------------------------------- 重训 job

@router.post("/retrain")
def start_retrain(req: GnnRetrainRequest):
    """启动反馈微调 job（dphuanjing 环境缺失时 503）。"""
    jobs = _jobs()
    try:
        job = jobs.start_retrain(
            feedback_ids=req.feedback_ids, freeze=req.freeze,
            epochs=req.epochs, lr=req.lr, batch_size=req.batch_size,
            patience=req.patience, feedback_pos_w=req.feedback_pos_w)
    except RuntimeError as exc:
        raise HTTPException(503 if "dphuanjing" in str(exc) or "训练资产" in str(exc)
                            else 409, str(exc))
    return job


@router.get("/retrain")
def list_retrain_jobs():
    jobs = _jobs()
    out = jobs.list_jobs()
    return {"jobs": out, "count": len(out)}


@router.get("/retrain/{job_id}")
def retrain_job_status(job_id: str):
    jobs = _jobs()
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(404, f"任务不存在: {job_id}")
    job["log_tail"] = jobs.job_log_tail(job_id, n=30)
    return job


@router.post("/retrain/{job_id}/cancel")
def cancel_retrain(job_id: str):
    jobs = _jobs()
    if not jobs.cancel_job(job_id):
        raise HTTPException(404, f"任务不存在: {job_id}")
    return {"cancelled": True, "job_id": job_id}


# ---------------------------------------------------------------- 版本管理

@router.get("/env")
def gnn_env():
    """训练环境可用性（前端面板置灰依据）。"""
    jobs = _jobs()
    env = jobs.env_ready()
    env["active_version"] = jobs.active_version()
    return env


@router.get("/versions")
def gnn_versions():
    jobs = _jobs()
    return {"active": jobs.active_version(), "versions": jobs.list_versions()}


@router.post("/versions/{version}/activate")
def activate_gnn_version(version: str):
    """切换/回退激活版本（base=gnn_v5.4 或 registry 内已有版本）。"""
    jobs = _jobs()
    reg = jobs.activate_version(version)
    if reg is None:
        raise HTTPException(404, f"版本不存在或不可用: {version}")
    return {"active": reg.get("active"), "message": f"已切换到 {reg.get('active')}"}


@router.get("/versions/{version}/compare")
def compare_gnn_version(version: str):
    """目标版本 vs 当前激活：反馈对逐对打分 + 金标准指标（同步计算）。"""
    import subprocess
    import sys as _sys
    try:
        from src import runtime_config
    except ImportError:
        import runtime_config  # type: ignore
    jobs = _jobs()
    if version != "gnn_v5.4":
        known = any(str(v.get("version")) == version
                    for v in jobs.list_versions())
        if not known:
            raise HTTPException(404, f"版本不存在: {version}")
    script = runtime_config.resource_root() / "gnn_training" / "compare_versions.py"
    if not script.is_file():
        raise HTTPException(503, "对比脚本缺失（gnn_training/compare_versions.py）")
    python = _sys.executable
    result = subprocess.run(
        [python, str(script), "--version", version,
         "--repo", str(runtime_config.resource_root())],
        cwd=str(runtime_config.resource_root()),
        capture_output=True, timeout=3600)
    if result.returncode != 0:
        raise HTTPException(500, "版本对比失败："
                            + result.stderr.decode("utf-8", errors="replace")[-500:])
    try:
        return json.loads(result.stdout.decode("utf-8"))
    except Exception as exc:
        raise HTTPException(500, f"对比结果解析失败：{exc}")
