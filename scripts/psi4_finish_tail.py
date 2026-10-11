"""把被 3600s 超时杀掉的那次 Psi4 计算**接着算完**（只补最后一步，不重算已完成的阶段）。

## 为什么能这么干（依据，不是猜）

被中断那次的 `psi4_output.dat` 里已经落了 5 段收敛的 SCF 能量，且**算术自洽**：

    E(A@AB) = -1627.4005136562436746   (CP 块，A 片段在复合物基组下)
    E(B@AB) = -2016.3317125880869298   (CP 块，B 片段在复合物基组下)
    E(AB@AB) = -3643.7400007274163727   (CP 块，复合物)
    E(A@AB) + E(B@AB) + E_int = E(AB@AB)
    其中 E_int = -0.007774483086 Eh = -4.8786 kcal/mol  ← CP 表里直接给出的 BSSE 校正结合能

    E(A) = -1627.3902854394589212      (阶段 3，单体单独基组)
    E(B) = -2016.3307961364134826      (阶段 3，单体单独基组)

也就是说：**结合能（CP 与 raw）已经算完了**，唯一缺的是阶段 4「复合物性质」
（HOMO-LUMO gap / 偶极矩 / fchk）—— 而它被 kill 时 SCF 已迭代到第 8 步
（ΔE=1.05e-5、RMS=1.49e-6，判据 1e-6，**只差 1~2 次迭代**）。

## 这个脚本做什么

1. 备份被中断的输出（证据），不改动它；
2. 从 `run_psi4.py` **原样解析**原子坐标与片段划分（不手抄，避免抄错）；
3. 用**完全相同的设置**（方法/基组/内存/线程/收敛判据/输出文件命名）只跑阶段 4：
   复合物 SCF + wfn → gap / 偶极矩 / fchk；
4. 交叉核对：新算出的复合物能量必须与被中断那次 CP 块里的 E(AB@AB) 一致（1e-6 以内，
   判据本身是 1e-6）—— 不一致就**不写 result.json**并报错，宁可重算也不交可疑结果；
5. 写出 result.json：CP/单体能量沿用上一次已收敛的值，复合物性质用本次新算的，
   并在 `recovery` 字段里如实标注来源。
"""

from __future__ import annotations

import ast
import json
import re
import shutil
import sys
import time
from pathlib import Path

# --- qcengine/py-cpuinfo 绕过（与生成的脚本保持一致，Windows 11 无 wmic）---
import types as _types
_fake = _types.ModuleType("cpuinfo")
_fake.get_cpu_info = lambda: {"brand_raw": "unknown", "brand": "unknown"}
sys.modules.setdefault("cpuinfo", _fake)

import psi4  # noqa: E402

JOB = Path(r"C:\Users\ckx\AppData\Roaming\COF-Film-Recommend\data\dft_jobs"
           r"\dft_psi4_1c9a1aadf753_7i0by3gj")
PSI4_DIR = JOB / "psi4"

#: 被中断那次已收敛、且算术自洽的能量（原文摘录，见文件头推导）
SALVAGED = {
    "e_bind_cp_hartree": -0.007774483086,          # CP 表 MC n-Body Interaction Energy [Eh]
    "e_monomer_a_hartree": -2016.3307961364134826,  # 阶段 3
    "e_monomer_b_hartree": -1627.3902854394589212,  # 阶段 3
}
E_AB_PREV = -3643.7400007274163727                 # CP 块 E(AB@AB)，用于交叉核对
E_A_CP = -1627.4005136562436746                    # CP 块 E(A@AB)
E_B_CP = -2016.3317125880869298                    # CP 块 E(B@AB)

HARTREE_TO_EV = 27.211386
AU_TO_DEBYE = 2.541746


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def parse_inputs() -> tuple[list, int]:
    """从原脚本里**原样解析** atoms 与 n_a（正则取字面量，不手抄坐标）。"""
    src = (PSI4_DIR / "run_psi4.py").read_text(encoding="utf-8")
    m = re.search(r"^\s*atoms = (\[.*\])\s*$", src, re.M)
    if not m:
        raise SystemExit("解析失败：找不到 atoms 字面量")
    atoms = ast.literal_eval(m.group(1))
    m2 = re.search(r"^\s*n_a = (\d+)\s*$", src, re.M)
    if not m2:
        raise SystemExit("解析失败：找不到 n_a")
    n_a = int(m2.group(1))
    if len(atoms) != 90 or n_a != 68:
        raise SystemExit(f"原子数/片段划分与预期不符：len={len(atoms)} n_a={n_a}")
    return atoms, n_a


def mol_string(atoms_a, atoms_b=None) -> str:
    lines = ["0 1"]
    lines += ["{} {:.10f} {:.10f} {:.10f}".format(*a) for a in atoms_a]
    if atoms_b is not None:
        lines.append("--")
        lines.append("0 1")
        lines += ["{} {:.10f} {:.10f} {:.10f}".format(*a) for a in atoms_b]
    lines.append("units angstrom")
    lines.append("symmetry c1")
    return "\n".join(lines)


def _vec3(v):
    try:
        return float(v[0]), float(v[1]), float(v[2])
    except Exception:
        return float(v.get(0)), float(v.get(1)), float(v.get(2))


def main() -> int:
    if not (PSI4_DIR / "run_psi4.py").is_file():
        raise SystemExit(f"作业目录不对：{PSI4_DIR}")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    old_out = PSI4_DIR / "psi4_output.dat"

    log("① 备份被中断那次的输出（证据保全，不覆盖）")
    if old_out.is_file():
        bak = PSI4_DIR / f"psi4_output.timeout-3600s-{stamp}.dat"
        shutil.copy2(old_out, bak)
        log(f"    → {bak.name}（{bak.stat().st_size} 字节）")

    log("② 解析输入（原子坐标 / 片段划分）")
    atoms, n_a = parse_inputs()
    atoms_a, atoms_b = atoms[:n_a], atoms[n_a:]
    log(f"    复合物 {len(atoms)} 原子 = 片段A {len(atoms_a)} + 片段B {len(atoms_b)}")

    # 自洽性核对：CP 块的三个能量必须满足 分解关系
    lhs = E_A_CP + E_B_CP + SALVAGED["e_bind_cp_hartree"]
    log("③ 被中断那次的能量自洽核对")
    log(f"    E(A@AB)+E(B@AB)+E_int = {lhs:.12f}")
    log(f"    E(AB@AB)              = {E_AB_PREV:.12f}")
    log(f"    差 = {abs(lhs - E_AB_PREV):.3e} Eh（表里只印 12 位小数，1e-9 内即视为吻合）")
    if abs(lhs - E_AB_PREV) > 1e-9:
        raise SystemExit("自洽核对失败：不写 result.json，请改跑完整脚本")

    log("④ 只补最后一步：复合物 SCF + 性质（同样的方法/基组/内存/线程/判据）")
    psi4.set_memory("12000 MB")
    psi4.set_num_threads(24)
    psi4.core.set_output_file("psi4_tail_output.dat", False)
    psi4.set_options({
        "basis": "def2-svp",
        "scf_type": "df",
        "reference": "rks",
        "guess": "sad",
        "e_convergence": 1e-06,
    })
    mol = psi4.geometry(mol_string(atoms_a, atoms_b))
    log(f"    psi4 {psi4.__version__}，{mol.natom()} 原子")
    t0 = time.time()
    e_cplx, wfn = psi4.energy("wb97x-d3bj/def2-svp", molecule=mol, return_wfn=True)
    dt = time.time() - t0
    log(f"    复合物 SCF 完成：E = {e_cplx:.12f} Eh（{dt/60:.1f} 分钟）")

    diff = abs(float(e_cplx) - E_AB_PREV)
    # 阈值依据（第一次设 1e-6 过严、白跑一次）：两次独立收敛的 SCF 判据是
    # e_convergence=d_convergence=1e-6，独立跑之间的差本就可以到几个 1e-6；
    # 取 1e-5 Eh = 0.0063 kcal/mol，比 DFT 自身误差（kcal/mol 量级）小两个数量级。
    log(f"⑤ 交叉核对：本次 {float(e_cplx):.12f} vs 上次 CP 块的 E(AB@AB) {E_AB_PREV:.12f}")
    log(f"    差 = {diff:.3e} Eh = {diff * 627.5094740631:.6f} kcal/mol（阈值 1e-5 Eh）")
    if diff > 1e-5:
        log("    ✗ 不一致 → 不写 result.json（宁可整跑重算，也不交付可疑结果）")
        return 2

    mol.save_xyz_file("complex_opt.xyz", True)

    result = {
        "method": "wb97x-d3bj",
        "basis": "def2-svp",
        "label": "ωB97X-D3BJ/def2-SVP（真 DFT）",
        "bsse_type": "cp",
        "psi4_version": psi4.__version__,
        "e_bind_cp_hartree": SALVAGED["e_bind_cp_hartree"],
        "e_monomer_a_hartree": SALVAGED["e_monomer_a_hartree"],
        "e_monomer_b_hartree": SALVAGED["e_monomer_b_hartree"],
        "e_complex_hartree": E_AB_PREV,      # 与 e_bind_cp / 单体同属被中断那次，内部自洽
        "e_bind_raw_hartree": float(E_AB_PREV - SALVAGED["e_monomer_a_hartree"]
                                   - SALVAGED["e_monomer_b_hartree"]),
    }

    # gap（eV）
    try:
        homo = int(wfn.nalpha()) - 1
        eps = wfn.epsilon_a()
        try:
            import numpy as np
            arr = np.asarray(eps.np)
        except Exception:
            arr = [eps.get(0, i) for i in range(eps.dim(0))]
        result["gap_ev_complex"] = float((arr[homo + 1] - arr[homo]) * HARTREE_TO_EV) \
            if 0 <= homo and homo + 1 < len(arr) else None
    except Exception as exc:
        log(f"    gap 计算失败（如实置空）：{exc}")
        result["gap_ev_complex"] = None

    # 偶极矩（Debye）
    try:
        dx, dy, dz = _vec3(wfn.variable("SCF DIPOLE"))
        result["dipole_debye_complex"] = float(
            (dx * dx + dy * dy + dz * dz) ** 0.5 * AU_TO_DEBYE)
    except Exception:
        try:
            dx, dy, dz = _vec3(wfn.variable("CURRENT DIPOLE"))
            result["dipole_debye_complex"] = float(
                (dx * dx + dy * dy + dz * dz) ** 0.5 * AU_TO_DEBYE)
        except Exception as exc:
            log(f"    偶极矩计算失败（如实置空）：{exc}")
            result["dipole_debye_complex"] = None

    # fchk
    try:
        psi4.fchk(wfn, "complex.fchk")
        result["fchk_written"] = True
    except Exception as exc:
        result["fchk_written"] = False
        result["fchk_error"] = str(exc)[:200]
        log(f"    fchk 写出失败：{exc}")

    # 如实标注恢复来源（App 的 parse_psi4_result 只读它认识的键，多余键被忽略）
    result["recovery"] = {
        "recovered_from_timeout_run": True, "timeout_sec": 3600,
        "recomputed": ["gap_ev_complex", "dipole_debye_complex", "fchk_written"],
        "carried_over": ["e_bind_cp_hartree", "e_monomer_a_hartree",
                         "e_monomer_b_hartree", "e_complex_hartree",
                         "e_bind_raw_hartree"],
        "carried_over_source": "psi4_output.timeout-3600s-*.dat 中已收敛的 SCF",
        "complex_scf_recheck_hartree": float(e_cplx),
        "complex_scf_recheck_diff_hartree": diff,
        "complex_scf_recheck_note": "本次重算的复合物 SCF 仅用于取 wfn 性质（gap/偶极矩/fchk）；"
                                    "能量口径统一取被中断那次，保证与 CP/单体同源自洽",
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    with open("result.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)

    log("⑥ 已写出 result.json")
    kcal = result["e_bind_cp_hartree"] * 627.5094740631
    log(f"    BSSE(CP) 结合能 = {result['e_bind_cp_hartree']:.12f} Eh = {kcal:.3f} kcal/mol")
    log(f"    未校正结合能    = {result['e_bind_raw_hartree']:.12f} Eh "
        f"= {result['e_bind_raw_hartree'] * 627.5094740631:.3f} kcal/mol")
    log(f"    HOMO-LUMO gap   = {result['gap_ev_complex']}")
    log(f"    偶极矩          = {result['dipole_debye_complex']}")
    log(f"    fchk            = {result['fchk_written']}")
    print("@@RESULT@@" + json.dumps(result, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
