#!/usr/bin/env python
"""DFT 后端基准验证 v2（docs/DFT后端替换方案.md §四 步骤 5）：S66 小分子二聚体对比。

v2 协议（针对 v1 暴露的「UFF 初猜取向」瓶颈修正）：
  1) 每体系用 5 个随机取向（seed 42/101/202/303/404）分别构造复合物初猜，
     各自做 GFN2-xTB 几何优化，取复合物能量最低的取向；
  2) xTB 结合能 = 该最优取向的 E_complex - E_a - E_b（单体各优化一次）；
  3) Psi4 结合能 = 在该最优 xTB 几何上做 ωB97X-D3BJ/def2-SVP 单点 + CP 校正
     （compute_pair_binding_psi4(optimize=False, complex_xyz=最优几何)）；
  4) 水二聚体另跑一次 optimize=True 全 Psi4 优化，抽查完整管线。

对比 5 个 S66 基准集体系（文献 CCSD(T)/CBS 结合能，Řezáč et al. JCTC 2011）。

口径（务必随结果一起引用）：
  - 几何：5 取向 UFF 初猜 → GFN2-xTB 优化取最低能（非 S66 平衡几何！
    psi4_optimize=True 的水二聚体为全 Psi4 优化口径，作为完整管线抽查）
  - Psi4 能量：ωB97X-D3BJ/def2-SVP 单点 + counterpoise BSSE 校正
  - 文献值：S66 平衡几何上的 CCSD(T)/CBS——几何来源不同是本对比的主要系统偏差

用法（项目根，约 15-25 分钟，Psi4 为分钟级计算）：
    E:/ANACONDA/python.exe scripts/benchmark_dft.py
产物：docs/benchmark_dft_results.json（结构化结果，供 docs/DFT基准验证.md 引用）
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.dft import engine, psi4_backend  # noqa: E402

# S66 参考值（kcal/mol，CCSD(T)/CBS；Řezáč et al. J. Chem. Theory Comput. 7, 2427 (2011)）
SYSTEMS = [
    {"key": "water_dimer", "name": "水二聚体", "a": "O", "b": "O",
     "s66_kcal": -5.02, "s66_note": "S66 #01，氢键"},
    {"key": "formamide_dimer", "name": "甲酰胺二聚体", "a": "NC=O", "b": "NC=O",
     "s66_kcal": -16.12, "s66_note": "S66，环形双氢键"},
    {"key": "benzene_water", "name": "苯···水", "a": "c1ccccc1", "b": "O",
     "s66_kcal": -3.28, "s66_note": "S66，OH-π"},
    {"key": "benzene_dimer_pd", "name": "苯二聚体", "a": "c1ccccc1", "b": "c1ccccc1",
     "s66_kcal": -2.74, "s66_note": "S66，平行错位 π-π"},
    {"key": "phenol_dimer", "name": "苯酚二聚体", "a": "Oc1ccccc1", "b": "Oc1ccccc1",
     "s66_kcal": -7.05, "s66_note": "S66，氢键+π"},
]

# 取向筛选种子与 xTB 方法档
SEEDS = [42, 101, 202, 303, 404]
XTB_METHOD = "gfn2"

# 水二聚体额外跑一次全 Psi4 优化（optimize=True），抽查完整管线
FULL_OPT_KEYS = {"water_dimer"}

OUT = ROOT / "docs" / "benchmark_dft_results.json"
JOBS_ROOT = ROOT / "data" / "tmp" / "bench_jobs"


def xtb_opt_energy(xyz: str, workdir: Path) -> tuple[float, str]:
    """对 xyz 初猜做 GFN2-xTB 几何优化，返回 (能量 hartree, 优化后 xyz)。"""
    args = engine.METHODS[XTB_METHOD]["args"]
    out, opt_xyz = engine._run_xtb(
        xyz, args, workdir, engine.method_timeout(XTB_METHOD))
    e = engine.parse_energy(out)
    if e is None:
        raise engine.DftError("xtb 输出中未找到总能量（TOTAL ENERGY）")
    return e, (opt_xyz or xyz)


def screen_orientations(smiles_a: str, smiles_b: str, key: str) -> dict:
    """5 取向 xTB 筛选：返回最优取向的 {e_complex, xyz, seed, all_energies}。"""
    best: dict | None = None
    trials = []
    for seed in SEEDS:
        t0 = time.time()
        guess = engine.embed_complex_xyz(smiles_a, smiles_b, seed=seed)
        workdir = JOBS_ROOT / key / f"orient_{seed}"
        workdir.mkdir(parents=True, exist_ok=True)
        e_c, opt_xyz = xtb_opt_energy(guess, workdir)
        trials.append({"seed": seed, "e_complex_hartree": e_c,
                       "elapsed_sec": round(time.time() - t0, 1)})
        print(f"[bench]   {key} seed={seed} E_c={e_c:.6f} Eh", flush=True)
        if best is None or e_c < best["e_complex_hartree"]:
            best = {"e_complex_hartree": e_c, "xyz": opt_xyz, "seed": seed}
    best["trials"] = trials
    return best


def run_system(sd: dict) -> dict:
    entry = {"key": sd["key"], "name": sd["name"],
             "smiles_a": sd["a"], "smiles_b": sd["b"],
             "s66_kcal": sd["s66_kcal"], "s66_note": sd["s66_note"]}

    # 单体能量（同分子只算一次；与 engine.compute_pair_binding 同口径：各自 xTB 优化）
    t0 = time.time()
    e_a, _ = xtb_opt_energy(engine.embed_monomer_xyz(sd["a"]),
                            JOBS_ROOT / sd["key"] / "monomer_a")
    e_b = e_a if sd["b"] == sd["a"] else xtb_opt_energy(
        engine.embed_monomer_xyz(sd["b"]), JOBS_ROOT / sd["key"] / "monomer_b")[0]
    entry["monomers"] = {"e_a_hartree": e_a, "e_b_hartree": e_b,
                         "elapsed_sec": round(time.time() - t0, 1)}
    print(f"[bench] {sd['name']} 单体完成 E_a={e_a:.6f} E_b={e_b:.6f}", flush=True)

    # 5 取向 xTB 筛选
    print(f"[bench] {sd['name']} 5 取向 xTB 筛选 …", flush=True)
    t0 = time.time()
    best = screen_orientations(sd["a"], sd["b"], sd["key"])
    e_bind_xtb = (best["e_complex_hartree"] - e_a - e_b) * engine.HARTREE_TO_KCAL
    entry["orientation_screen"] = {
        "seeds": SEEDS, "best_seed": best["seed"], "trials": best["trials"],
        "elapsed_sec": round(time.time() - t0, 1)}
    entry["xtb"] = {"ok": True, "e_bind_kcal": round(e_bind_xtb, 3),
                    "error_kcal": round(e_bind_xtb - sd["s66_kcal"], 3)}
    print(f"[bench] {sd['name']} 最优取向 seed={best['seed']} "
          f"xTB E_bind={e_bind_xtb:.2f} kcal/mol", flush=True)

    # Psi4 单点 CP（注入最优 xTB 几何）
    print(f"[bench] {sd['name']} Psi4 单点CP（最优几何）…", flush=True)
    t0 = time.time()
    try:
        r = psi4_backend.compute_pair_binding_psi4(
            sd["a"], sd["b"], jobs_root=JOBS_ROOT / sd["key"] / "psi4",
            optimize=False, complex_xyz=best["xyz"])
        entry["psi4"] = {"ok": True, "e_bind_kcal": r["e_bind_kcal"],
                         "error_kcal": round(r["e_bind_kcal"] - sd["s66_kcal"], 3),
                         "elapsed_sec": round(time.time() - t0, 1),
                         "detail": r.get("psi4_detail")}
    except Exception as exc:  # noqa: BLE001 —— 基准要记录失败而非中断
        entry["psi4"] = {"ok": False, "error": str(exc)[:300],
                         "elapsed_sec": round(time.time() - t0, 1)}
    print(f"[bench] {sd['name']} Psi4: "
          f"{entry['psi4'].get('e_bind_kcal', entry['psi4'].get('error'))}",
          flush=True)

    # 水二聚体全 Psi4 优化抽查（默认 UFF 初猜口径，对应完整产品管线）
    if sd["key"] in FULL_OPT_KEYS:
        print(f"[bench] {sd['name']} Psi4 全优化抽查 …", flush=True)
        t0 = time.time()
        try:
            r = psi4_backend.compute_pair_binding_psi4(
                sd["a"], sd["b"], jobs_root=JOBS_ROOT / sd["key"] / "psi4_full",
                optimize=True)
            entry["psi4_full_opt"] = {
                "ok": True, "e_bind_kcal": r["e_bind_kcal"],
                "error_kcal": round(r["e_bind_kcal"] - sd["s66_kcal"], 3),
                "elapsed_sec": round(time.time() - t0, 1)}
        except Exception as exc:  # noqa: BLE001
            entry["psi4_full_opt"] = {"ok": False, "error": str(exc)[:300],
                                      "elapsed_sec": round(time.time() - t0, 1)}
        print(f"[bench] {sd['name']} Psi4 全优化: "
              f"{entry['psi4_full_opt'].get('e_bind_kcal')}", flush=True)

    return entry


def main() -> None:
    print(f"[bench] psi4 检测: {psi4_backend.detect_psi4()}", flush=True)
    results = {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
               "protocol": {
                   "version": "v2（5 取向 xTB 筛选 + Psi4 CP 单点）",
                   "geometry": f"{len(SEEDS)} 取向 UFF 初猜（seed {SEEDS}）→ 各自 "
                               "GFN2-xTB 优化，取复合物能量最低者（非 S66 几何）",
                   "xtb": "GFN2-xTB：E_bind = E_complex(最优取向) - E_a - E_b",
                   "psi4": "ωB97X-D3BJ/def2-SVP 单点 + counterpoise BSSE"
                           "（最优 xTB 几何上，optimize=False）",
                   "psi4_full_opt_keys": sorted(FULL_OPT_KEYS),
                   "reference": "S66 CCSD(T)/CBS（Řezáč et al. JCTC 2011）",
               },
               "systems": []}

    for sd in SYSTEMS:
        try:
            entry = run_system(sd)
        except Exception as exc:  # noqa: BLE001 —— 单体系失败不中断整体
            entry = {"key": sd["key"], "name": sd["name"],
                     "smiles_a": sd["a"], "smiles_b": sd["b"],
                     "s66_kcal": sd["s66_kcal"], "s66_note": sd["s66_note"],
                     "ok": False, "error": str(exc)[:300]}
            print(f"[bench] {sd['name']} 失败: {exc}", flush=True)
        results["systems"].append(entry)
        OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2),
                       encoding="utf-8")

    print(f"[bench] 全部完成 → {OUT}", flush=True)


if __name__ == "__main__":
    main()
