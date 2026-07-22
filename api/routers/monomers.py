"""单体路由：内置库 + 性质卡（RDKit 事实 + LLM 解读）。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..deps import load_builtin_monomers
from ..schemas import PropsBatchRequest

router = APIRouter(prefix="/api/monomers", tags=["monomers"])


@router.get("")
def list_monomers():
    lib = load_builtin_monomers()
    return {"aldehydes": lib["aldehydes"], "amines": lib["amines"]}


@router.post("/props/batch")
def monomer_props_batch(req: PropsBatchRequest):
    """批量性质卡：逐项调 get_monomer_properties。

    单项非法 SMILES → 该项返回 {"smiles", "name", "error"}，不影响其他项；
    与单体接口同口径：RDKit 预校验拦截非法 SMILES，绝不烧 LLM。
    """
    try:
        from rdkit import Chem
        def _valid(s: str) -> bool:
            return Chem.MolFromSmiles(s) is not None
    except ImportError:
        def _valid(s: str) -> bool:  # RDKit 不可用时降级为后端自行兜底
            return True

    from recommend.monomer_props import canonical_smiles, get_monomer_properties

    results: list[dict] = []
    for item in req.items:
        smiles = (item.smiles or "").strip()
        name = (item.name or "").strip()
        if not smiles:
            results.append({"smiles": smiles, "name": name,
                            "error": "smiles 不能为空"})
            continue
        if not _valid(smiles):
            results.append({"smiles": smiles, "name": name,
                            "error": f"非法 SMILES，RDKit 解析失败: {smiles!r}"})
            continue
        try:
            card = get_monomer_properties(canonical_smiles(smiles), name=name)
            card["name"] = card.get("name") or name
            results.append(card)
        except Exception as exc:
            results.append({"smiles": smiles, "name": name,
                            "error": f"{type(exc).__name__}: {exc}"})
    return {"results": results, "count": len(results)}


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
