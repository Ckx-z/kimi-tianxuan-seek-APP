#!/usr/bin/env python
"""真实场景端到端基准：TAPT–DMTA 亚胺二聚体 vs PBDEs（刘璐 2021 口径对齐）。

场景（docs/DFT基准验证.md 第七节）：三嗪核 COF（TAPT + DMTA 缩合）吸附多溴联苯醚。
  - 主体：TAPT（2,4,6-三(4-氨基苯基)三嗪）+ DMTA（2,5-二甲氧基对苯二甲醛）
    经 dimer 反应模板生成亚胺二聚体（示意单点缩合）
  - 客体：BDE154（2,2',4,4',5,6'-六溴联苯醚，PubChem CID 15509898）
          BDE47（2,2',4,4'-四溴联苯醚，PubChem CID 95170）
  - 文献 E_ads（刘璐 2021 J. Hazard. Mater. 403, 123917，COMPASSII MC + DMol3
    周期性模型 + B3LYP/6-31G(d,p) 精修）：BDE47 −31.06、BDE154 −48.47 kJ/mol

口径（务实版，分子团簇 vs 文献周期性模型，数量级与排序正确即成功）：
  1) MC 取向采样（基序模板含卤键取向 + Metropolis/UFF）→ GFN-FF 分级筛选
  2) 最优初猜 GFN2-xTB 全优化 → xTB 结合能
  3) 该几何上 Psi4 单点 + counterpoise BSSE（optimize=False）：
     precision 档 ωB97X-D3BJ/def2-SVP 与 literature 档 B3LYP/6-31G(d,p) 各算一次

用法（项目根；Psi4 为十分钟级计算，整体可能 1 小时以上，建议后台跑）：
    E:/ANACONDA/python.exe scripts/benchmark_cof_pbde.py
产物：docs/benchmark_cof_pbde_results.json（增量写盘，中断可续看）
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

# 大体系：放宽 xTB / Psi4 超时（必须在调用前设置，读取发生在调用时）
os.environ.setdefault("COF_DFT_TIMEOUT_GFN2", "1800")
os.environ.setdefault("COF_DFT_TIMEOUT_GFNFF", "600")
os.environ.setdefault("COF_DFT_TIMEOUT_PSI4", "5400")
# Psi4 scratch 默认落系统盘 Temp；大体系 DF 张量可达 GB 级，引导到数据盘
#（C: 曾因此写满导致 libpsio error.cc:142）
if Path("E:/").exists():
    Path("E:/psi4_scratch").mkdir(exist_ok=True)
    os.environ.setdefault("PSI_SCRATCH", "E:/psi4_scratch")

from src.dft import dimer as dimer_mod  # noqa: E402
from src.dft import engine, psi4_backend  # noqa: E402

# 单体与客体（PubChem CanonicalSMILES）
TAPT = "Nc1ccc(-c2nc(-c3ccc(N)cc3)nc(-c3ccc(N)cc3)n2)cc1"  # 2,4,6-三(4-氨基苯基)三嗪
DMTA = "COc1cc(C=O)c(OC)cc1C=O"  # 2,5-二甲氧基对苯二甲醛
GUESTS = [
    {"key": "bde154", "name": "BDE-154（六溴联苯醚）",
     "smiles": "C1=C(C=C(C(=C1Br)OC2=CC(=C(C=C2Br)Br)Br)Br)Br",
     "ref_kj": -48.47, "n_br": 6},
    {"key": "bde47", "name": "BDE-47（四溴联苯醚）",
     "smiles": "C1=CC(=C(C=C1Br)Br)OC2=C(C=C(C=C2)Br)Br",
     "ref_kj": -31.06, "n_br": 4},
]

OUT = ROOT / "docs" / "benchmark_cof_pbde_results.json"
JOBS_ROOT = ROOT / "data" / "tmp" / "bench_pbde_jobs"


def xtb_opt(xyz: str, workdir: Path, method: str = "gfn2") -> tuple[float, str]:
    args = engine.METHODS[method]["args"]
    out, opt_xyz = engine._run_xtb(xyz, args, workdir, engine.method_timeout(method))
    e = engine.parse_energy(out)
    if e is None:
        raise engine.DftError("xtb 输出中未找到总能量（TOTAL ENERGY）")
    return e, (opt_xyz or xyz)


def run_guest(dimer_smiles: str, guest: dict, e_dimer: float,
              results: dict) -> dict:
    entry = {"key": guest["key"], "name": guest["name"],
             "smiles": guest["smiles"], "ref_kj": guest["ref_kj"],
             "n_br": guest["n_br"], "e_dimer_hartree": e_dimer}
    wd = JOBS_ROOT / guest["key"]
    wd.mkdir(parents=True, exist_ok=True)

    # 1) 单体能量（二聚体全体系只算一次，由调用方传入缓存）
    t0 = time.time()
    e_g, _ = xtb_opt(engine.embed_monomer_xyz(guest["smiles"]),
                     wd / "monomer_guest")
    entry["guest_e_hartree"] = e_g
    print(f"[pbde] {guest['name']} 单体 E={e_g:.6f} Eh "
          f"({time.time() - t0:.0f}s)", flush=True)

    # 2) MC 取向采样 + GFN-FF 分级筛选
    print(f"[pbde] {guest['name']} MC 取向采样 + 筛选 …", flush=True)
    t0 = time.time()
    info = engine.screen_complex_xtb(dimer_smiles, guest["smiles"],
                                     wd / "screen")
    entry["screen"] = {"n_samples": info["n_samples"],
                       "best_kind": info["best_kind"],
                       "screen_level": info["screen_level"],
                       "elapsed_sec": round(time.time() - t0, 1),
                       "trials": info["trials"]}
    print(f"[pbde] {guest['name']} 筛选完成：最优 {info['best_kind']} "
          f"({info['n_samples']} 候选, {time.time() - t0:.0f}s)", flush=True)

    # 3) 最优初猜 GFN2 全优化 → xTB 结合能
    t0 = time.time()
    e_c, xyz_c_opt = xtb_opt(info["best_xyz"], wd / "complex_gfn2")
    e_bind_xtb_kj = (e_c - e_dimer - e_g) * engine.HARTREE_TO_KJ
    entry["xtb"] = {"e_bind_kj": round(e_bind_xtb_kj, 2),
                    "e_bind_kcal": round(e_bind_xtb_kj / 4.184, 3),
                    "error_kj": round(e_bind_xtb_kj - guest["ref_kj"], 2),
                    "elapsed_sec": round(time.time() - t0, 1)}
    print(f"[pbde] {guest['name']} xTB E_bind={e_bind_xtb_kj:.2f} kJ/mol "
          f"(文献 {guest['ref_kj']})", flush=True)
    (wd / "complex_gfn2_opt.xyz").write_text(xyz_c_opt, encoding="utf-8")

    # 4) Psi4 单点 CP（xTB 优化几何注入；precision / literature 两档）
    for preset, mkey in (("precision", "wb97xd3bj_svp"),
                         ("literature", "b3lyp_631gdp")):
        print(f"[pbde] {guest['name']} Psi4 {preset}（{mkey}）单点 CP …",
              flush=True)
        t0 = time.time()
        try:
            r = psi4_backend.compute_pair_binding_psi4(
                dimer_smiles, guest["smiles"], method=mkey,
                jobs_root=wd / f"psi4_{preset}",
                optimize=False, complex_xyz=xyz_c_opt)
            entry[f"psi4_{preset}"] = {
                "ok": True, "e_bind_kj": r["e_bind_kj"],
                "e_bind_kcal": r["e_bind_kcal"],
                "error_kj": round(r["e_bind_kj"] - guest["ref_kj"], 2),
                "elapsed_sec": round(time.time() - t0, 1),
                "detail": r.get("psi4_detail")}
            print(f"[pbde] {guest['name']} Psi4 {preset}: "
                  f"{r['e_bind_kj']:.2f} kJ/mol ({time.time() - t0:.0f}s)",
                  flush=True)
        except Exception as exc:  # noqa: BLE001 —— 基准记录失败而非中断
            entry[f"psi4_{preset}"] = {"ok": False, "error": str(exc)[:300],
                                       "elapsed_sec": round(time.time() - t0, 1)}
            print(f"[pbde] {guest['name']} Psi4 {preset} 失败: {exc}",
                  flush=True)
        # 每个档位完成后立即落盘（长跑中断保护）
        OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2),
                       encoding="utf-8")
    return entry


def main() -> None:
    print(f"[pbde] psi4 检测: {psi4_backend.detect_psi4()}", flush=True)
    dim = dimer_mod.make_dimer(DMTA, TAPT)  # 醛=DMTA，胺=TAPT
    dimer_smiles = dim["smiles"]
    n_atoms_dimer = engine._xyz_atom_count(engine.embed_monomer_xyz(dimer_smiles))
    print(f"[pbde] 亚胺二聚体: {dimer_smiles}（{n_atoms_dimer} 原子"
          f"{'，多位点示意单点缩合' if dim.get('multi_site') else ''}）",
          flush=True)

    results = {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
               "protocol": {
                   "scenario": "TAPT–DMTA 亚胺二聚体 vs PBDEs（刘璐 2021 JHM "
                               "403, 123917 场景对齐）",
                   "geometry": "MC 取向采样（基序模板含卤键 + Metropolis/UFF）→ "
                               "GFN-FF 分级筛选 → GFN2-xTB 全优化（分子团簇，"
                               "非周期性模型）",
                   "xtb": "GFN2-xTB：E_bind = E_complex - E_dimer - E_guest",
                   "psi4": "xTB 优化几何上单点 + counterpoise BSSE"
                           "（optimize=False）；precision=ωB97X-D3BJ/def2-SVP，"
                           "literature=B3LYP/6-31G(d,p)",
                   "reference": "刘璐 2021：COMPASSII MC + DMol3 周期性 E_ads + "
                                "B3LYP/6-31G(d,p) 精修；BDE47 −31.06、"
                                "BDE154 −48.47 kJ/mol",
                   "note": "文献为周期性框架多层吸附口径，本计算为分子团簇；"
                           "数量级与 Br 数排序正确即算成功",
               },
               "dimer": {"ald": DMTA, "amine": TAPT, "smiles": dimer_smiles,
                         "multi_site": bool(dim.get("multi_site")),
                         "atom_count": n_atoms_dimer},
               "guests": []}

    # 二聚体 GFN2 能量（两个客体共用）
    t0 = time.time()
    print("[pbde] 二聚体 GFN2 优化 …", flush=True)
    e_d, _ = xtb_opt(engine.embed_monomer_xyz(dimer_smiles),
                     JOBS_ROOT / "monomer_dimer")
    print(f"[pbde] 二聚体 E={e_d:.6f} Eh ({time.time() - t0:.0f}s)", flush=True)

    for guest in GUESTS:
        try:
            entry = run_guest(dimer_smiles, guest, e_d, results)
        except Exception as exc:  # noqa: BLE001
            entry = {"key": guest["key"], "name": guest["name"],
                     "ref_kj": guest["ref_kj"], "ok": False,
                     "error": str(exc)[:300]}
            print(f"[pbde] {guest['name']} 失败: {exc}", flush=True)
        results["guests"].append(entry)
        OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2),
                       encoding="utf-8")

    print(f"[pbde] 全部完成 → {OUT}", flush=True)


if __name__ == "__main__":
    main()
