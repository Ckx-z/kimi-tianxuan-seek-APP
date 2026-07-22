"""单体路由：内置库 + 性质卡（RDKit 事实 + LLM 解读）。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..deps import load_builtin_monomers

router = APIRouter(prefix="/api/monomers", tags=["monomers"])


@router.get("")
def list_monomers():
    lib = load_builtin_monomers()
    return {"aldehydes": lib["aldehydes"], "amines": lib["amines"]}


@router.get("/props")
def monomer_props(smiles: str, name: str = ""):
    smiles = smiles.strip()
    if not smiles:
        raise HTTPException(400, "smiles 不能为空")
    try:
        from recommend.monomer_props import get_monomer_properties
        return get_monomer_properties(smiles, name=name.strip())
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"性质卡生成失败：{type(exc).__name__}: {exc}")
