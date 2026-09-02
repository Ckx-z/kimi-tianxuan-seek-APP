"""DFT 引擎与方法文献引用注册表（作者/期刊/DOI）。

所有计算引擎与方法说明中引用的文献统一在此登记，供：
- 结果面板「方法引用」展示（DOI 可点击跳转 https://doi.org/...）
- 方法选择区（Dft 页 preset 描述）的 DOI 链接
- 导出模板（Gaussian/ORCA 输入文件注释）的纯文本引用行

新增引擎/方法时请同步在此登记引用；DOI 一律以 https://doi.org/<DOI>
形式访问，未登记 DOI 的文献不得编造（标注「暂无 DOI」）。
"""

from __future__ import annotations


def _ref(key: str, label: str, cite: str, doi: str) -> dict:
    """构造一条引用：{key, label, cite, doi, url}。"""
    return {
        "key": key,
        "label": label,
        "cite": cite,
        "doi": doi,
        "url": f"https://doi.org/{doi}" if doi else None,
    }


# ---- 基础引用条目 ----
_B3LYP_BECKE = _ref(
    "b3lyp-becke",
    "B3LYP（Becke 三参数杂化）",
    "Becke, A. D. Density-functional thermochemistry. III. The role of exact "
    "exchange. J. Chem. Phys. 1993, 98, 5648–5652.",
    "10.1063/1.464913")
_B3LYP_LYP = _ref(
    "b3lyp-lyp",
    "B3LYP（Lee–Yang–Parr 关联泛函）",
    "Lee, C.; Yang, W.; Parr, R. G. Development of the Colle-Salvetti "
    "correlation-energy formula into a functional of the electron density. "
    "Phys. Rev. B 1988, 37, 785–789.",
    "10.1103/PhysRevB.37.785")
_LIU2021 = _ref(
    "liu2021",
    "文献口径对齐（刘璐 2021 COF 吸附 DFT 研究）",
    "Liu, L.; Wang, X.-X.; Wang, X.; Xu, G.-J.; Zhao, Y.-F.; Wang, M.-L.; "
    "Lin, J.-M.; Zhao, R.-S.; Wu, Y. Triazine-cored covalent organic "
    "framework for ultrasensitive detection of polybrominated diphenyl "
    "ethers from real samples: Experimental and DFT study. "
    "J. Hazard. Mater. 2021, 403, 123917.",
    "10.1016/j.jhazmat.2020.123917")
_WB97XD = _ref(
    "wb97x-d",
    "ωB97X-D（长程校正 + 经验色散）",
    "Chai, J.-D.; Head-Gordon, M. Long-range corrected hybrid density "
    "functionals with damped atom–atom dispersion corrections. "
    "Phys. Chem. Chem. Phys. 2008, 10, 6615–6620.",
    "10.1039/b810189b")
_D3 = _ref(
    "d3",
    "DFT-D3 色散校正",
    "Grimme, S.; Antony, J.; Ehrlich, S.; Krieg, H. A consistent and "
    "accurate ab initio parametrization of density functional dispersion "
    "correction (DFT-D) for the 94 elements H-Pu. J. Chem. Phys. 2010, "
    "132, 154104.",
    "10.1063/1.3382344")
_D3BJ = _ref(
    "d3bj",
    "DFT-D3(BJ) Becke–Johnson 阻尼",
    "Grimme, S.; Ehrlich, S.; Goerigk, L. Effect of the damping function "
    "in dispersion corrected density functional theory. "
    "J. Comput. Chem. 2011, 32, 1456–1465.",
    "10.1002/jcc.21759")
_GFN2 = _ref(
    "gfn2-xtb",
    "GFN2-xTB 半经验方法",
    "Bannwarth, C.; Ehlert, S.; Grimme, S. GFN2-xTB—An accurate and broadly "
    "parametrized self-consistent tight-binding quantum chemical method "
    "with multipole electrostatics and density-dependent dispersion "
    "contributions. J. Chem. Theory Comput. 2019, 15, 1652–1671.",
    "10.1021/acs.jctc.8b01176")
_GFNFF = _ref(
    "gfn-ff",
    "GFN-FF 通用力场",
    "Spicher, S.; Grimme, S. Robust atomistic modeling of materials, "
    "organometallic, and biochemical systems. "
    "Angew. Chem. Int. Ed. 2020, 59, 15665–15673.",
    "10.1002/anie.202004239")
_XTB = _ref(
    "xtb",
    "xTB 程序",
    "Bannwarth, C.; Caldeweyher, E.; Ehlert, S.; Hansen, A.; Pracht, P.; "
    "Seibert, J.; Spicher, S.; Grimme, S. Extended tight-binding quantum "
    "chemistry methods. WIREs Comput. Mol. Sci. 2021, 11, e1493.",
    "10.1002/wcms.1493")
_CREST = _ref(
    "crest",
    "CREST 构象搜索（iMTD-GC）",
    "Pracht, P.; Bohle, F.; Grimme, S. Automated exploration of the "
    "low-energy chemical space with fast quantum chemical methods. "
    "Phys. Chem. Chem. Phys. 2020, 22, 7169–7192.",
    "10.1039/c9cp06869d")
_ETKDG = _ref(
    "etkdg",
    "ETKDG 构象生成",
    "Riniker, S.; Landrum, G. A. Better informed distance geometry: Using "
    "what we know to improve conformation generation. "
    "J. Chem. Inf. Model. 2015, 55, 2562–2574.",
    "10.1021/acs.jcim.5b00654")
_UFF = _ref(
    "uff",
    "UFF 力场（复合物预优化/摆位）",
    "Rappé, A. K.; Casewit, C. J.; Colwell, K. S.; Goddard, W. A.; "
    "Skiff, W. M. UFF, a full periodic table force field for molecular "
    "mechanics and molecular dynamics simulations. "
    "J. Am. Chem. Soc. 1992, 114, 10024–10035.",
    "10.1021/ja00051a040")
_PSI4 = _ref(
    "psi4",
    "Psi4 量子化学程序",
    "Smith, D. G. A.; Burns, L. A.; Simmonett, A. C.; Parrish, R. M.; "
    "et al. Psi4 1.4: Open-source software for high-throughput quantum "
    "chemistry. J. Chem. Phys. 2020, 152, 184108.",
    "10.1063/5.0006002")


# ---- 方法/引擎 key → 引用列表 ----
METHOD_CITATIONS: dict[str, list[dict]] = {
    # xTB 快速档（engine.py METHODS 的 key）
    "gfnff": [_GFNFF, _XTB],
    "gfn2": [_GFN2, _XTB],
    # Psi4 精度档（psi4_backend.PSI4_METHODS 的 key）
    "wb97xd3bj_svp": [_WB97XD, _D3, _D3BJ, _PSI4],
    "wb97xd3bj_svp_quick": [_WB97XD, _D3, _D3BJ, _PSI4],
    "b3lyp_631gdp": [_B3LYP_BECKE, _B3LYP_LYP, _LIU2021, _PSI4],
    # 构象采样引擎（sampling engine）
    "etkdg": [_ETKDG],
    "crest": [_CREST],
    "rigid": [],
    # 复合物摆位/预优化力场
    "uff": [_UFF],
}

# preset 别名 → 展示引用（Dft 页方法选择区直接展示，无需任务结果）
PRESET_CITATIONS: dict[str, list[dict]] = {
    "precision": [_WB97XD, _D3, _D3BJ, _PSI4],
    "batch": [_WB97XD, _D3, _D3BJ, _PSI4],
    "literature": [_B3LYP_BECKE, _B3LYP_LYP, _LIU2021, _PSI4],
}


def citations_for(method: str | None, backend: str | None = None,
                  sampling: str | None = None) -> list[dict]:
    """按方法 key（+ 可选后端/采样引擎）汇总引用；未知 key 返回空列表。

    - backend='psi4' 时 method 为 PSI4_METHODS key；
    - backend='xtb'（或 None）时 method 为 engine.METHODS key（gfnff/gfn2）；
    - sampling 为 conformers 采样引擎（etkdg/crest/rigid），附加对应引用。
    """
    out: list[dict] = []
    seen: set[str] = set()
    for key in ((method or "").strip(), (sampling or "").strip()):
        for ref in METHOD_CITATIONS.get(key, []):
            if ref["key"] not in seen:
                seen.add(ref["key"])
                out.append(ref)
    # 未命中任何方法 key 时兜底：Psi4 后端至少带 Psi4 程序引用
    if not out and backend == "psi4":
        out.append(_PSI4)
    return out


def citations_for_preset(preset: str) -> list[dict]:
    """Dft 页 preset 别名（precision/batch/literature）→ 展示引用。"""
    return list(PRESET_CITATIONS.get((preset or "").strip(), []))


def citations_text(method: str | None, backend: str | None = None,
                   sampling: str | None = None) -> list[str]:
    """导出模板用的纯文本引用行（含 DOI，可嵌入 Gaussian/ORCA 注释）。"""
    lines: list[str] = []
    for ref in citations_for(method, backend=backend, sampling=sampling):
        lines.append(f"{ref['cite']} DOI: {ref['doi']}.")
    return lines


def registry() -> dict:
    """全量注册表（GET /api/dft/citations 用）：方法与 preset 两级。"""
    return {
        "methods": {k: v for k, v in METHOD_CITATIONS.items()},
        "presets": {k: v for k, v in PRESET_CITATIONS.items()},
    }
