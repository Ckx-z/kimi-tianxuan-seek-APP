"""方案卡路由：模板列表 / docx 上传提取 / 方案卡生成。"""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile

from ..schemas import PlanCardRequest

router = APIRouter(prefix="/api", tags=["plan"])


@router.get("/plan-templates")
def list_templates():
    from recommend import plan_templates
    return {"templates": plan_templates.list_templates()}


@router.get("/plan-templates/{tpl_id}")
def get_template(tpl_id: str):
    from recommend import plan_templates
    try:
        return plan_templates.get_template(tpl_id)
    except Exception:
        raise HTTPException(404, f"模板 {tpl_id} 不存在")


@router.post("/plan-templates/upload", status_code=201)
async def upload_template(file: UploadFile, name: str = ""):
    """上传文献 docx → LLM 自动提取为方案卡模板。"""
    if not (file.filename or "").lower().endswith(".docx"):
        raise HTTPException(400, "仅支持 .docx 文件")
    from recommend import plan_templates
    tmp = Path(tempfile.mkdtemp()) / (file.filename or "upload.docx")
    try:
        tmp.write_bytes(await file.read())
        tpl = plan_templates.extract_template_from_docx(
            tmp, name=name or Path(file.filename or "").stem)
        return plan_templates.save_template(tpl)
    except plan_templates.TemplateError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"模板提取失败：{type(exc).__name__}: {exc}")
    finally:
        tmp.unlink(missing_ok=True)


@router.post("/plan-card")
def plan_card(req: PlanCardRequest):
    from recommend import plan_card, plan_templates
    template = None
    if req.template_id:
        try:
            template = plan_templates.get_template(req.template_id)
        except Exception:
            raise HTTPException(404, f"模板 {req.template_id} 不存在")
    try:
        return plan_card.generate_plan_card(
            req.aldehyde_smiles.strip(), req.amine_smiles.strip(),
            ald_name=req.ald_name.strip(), amine_name=req.amine_name.strip(),
            template=template)
    except Exception as exc:
        raise HTTPException(500, f"方案卡生成失败：{type(exc).__name__}: {exc}")
