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
    # 先用 RDKit 校验合法性：非法 SMILES 直接 400，绝不烧 LLM
    # （canonical_smiles 解析失败不抛异常，故合法性用 MolFromSmiles 判定，
    #   规范化仍走 canonical_smiles 以命中性质卡缓存）
    try:
        from rdkit import Chem
        if Chem.MolFromSmiles(smiles) is None:
            raise HTTPException(400, f"非法 SMILES，RDKit 解析失败: {smiles!r}")
    except ImportError:
        pass  # RDKit 不可用时降级为后端自行兜底（facts={}）
    try:
        from recommend.monomer_props import canonical_smiles, get_monomer_properties
        smiles = canonical_smiles(smiles)
        return get_monomer_properties(smiles, name=name.strip())
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"性质卡生成失败：{type(exc).__name__}: {exc}")
