"""单体路由：内置库 + 性质卡（RDKit 事实 + LLM 解读）。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response

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


@router.get("/structure.svg")
def monomer_structure(smiles: str, w: int = 360, h: int = 260):
    """单体 2D 结构图（RDKit SVG）。非法 SMILES → 400。

    供收藏详情等界面直接 <img src="/api/monomers/structure.svg?smiles=...">。
    """
    smiles = (smiles or "").strip()
    if not smiles:
        raise HTTPException(400, "smiles 不能为空")
    try:
        from rdkit import Chem
        from rdkit.Chem.Draw import rdMolDraw2D
    except ImportError:
        raise HTTPException(503, "RDKit 不可用，无法绘制结构图")
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise HTTPException(400, f"非法 SMILES，RDKit 解析失败: {smiles!r}")
    w = max(120, min(int(w), 800))
    h = max(90, min(int(h), 600))
    drawer = rdMolDraw2D.MolDraw2DSVG(w, h)
    drawer.DrawMolecule(mol)
    drawer.FinishDrawing()
    return Response(content=drawer.GetDrawingText(), media_type="image/svg+xml")


@router.get("/dimer.svg")
def dimer_structure(ald: str, amine: str, w: int = 600, h: int = 300):
    """醛胺缩合产物（二聚体骨架）示意图 SVG；反应无法示意 → 404。

    供查询打分页直接 <img src="/api/monomers/dimer.svg?ald=...&amine=...">。
    与单体结构图同走 rdMolDraw2D SVG（frozen 下已验证可用，不依赖 PIL）。
    """
    ald, amine = (ald or "").strip(), (amine or "").strip()
    if not ald or not amine:
        raise HTTPException(400, "ald / amine 不能为空")
    try:
        from rdkit import Chem
        from rdkit.Chem import AllChem
        from rdkit.Chem.Draw import rdMolDraw2D
    except ImportError:
        raise HTTPException(503, "RDKit 不可用，无法绘制结构图")
    ma, mm = Chem.MolFromSmiles(ald), Chem.MolFromSmiles(amine)
    if ma is None or mm is None:
        raise HTTPException(400, "非法 SMILES，RDKit 解析失败")
    try:
        rxn = AllChem.ReactionFromSmarts("[C:1]=O.[NH2:2]>>[C:1]=[N:2]")
        products = rxn.RunReactants((ma, mm))
        if not products:
            raise HTTPException(404, "该醛-胺组合无法生成缩合产物示意图")
        prod = products[0][0]
        Chem.SanitizeMol(prod)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(404, "该醛-胺组合无法生成缩合产物示意图")
    w = max(200, min(int(w), 900))
    h = max(120, min(int(h), 600))
    drawer = rdMolDraw2D.MolDraw2DSVG(w, h)
    drawer.DrawMolecule(prod)
    drawer.FinishDrawing()
    return Response(content=drawer.GetDrawingText(), media_type="image/svg+xml")


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
