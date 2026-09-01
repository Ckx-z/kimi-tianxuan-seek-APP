"""Psi4 精度档后端：真 DFT（ωB97X-D3BJ/def2-SVP）+ BSSE counterpoise 结合能。

分层架构（docs/DFT后端替换方案.md §三）中的精度档，与 engine.py（xTB 快速档）
并列，结果字段口径与 engine.compute_binding / compute_pair_binding 对齐，
前端展示与缓存/历史管线无需感知后端差异（结果多带 backend / psi4_detail 字段）。

管线（dimer 模式；pair 模式把二聚体生成换成直接取分子 A）：
    醛/胺单体 → 二聚体 D → 解析 X → D·X 复合物初猜（engine 多取向逻辑）
    → xTB 预优化得初始几何（engine._run_xtb，缺 xTB 时退化为 UFF 初猜）
    → psi4-env 子进程跑生成的 Python 脚本：
        （可选）psi4 几何优化 → cp 结合能 → 单体能 → 复合物 wfn（gap/偶极矩/fchk）
    → 解析 result.json 回填结果 dict

子进程约定（复用 GNN dphuanjing 隔离模式）：
  - psi4-env 解释器经 runtime_config 配置链解析（COF_PSI4_PYTHON >
    runtime.local.json pythons.psi4 > 自动探测 <conda>/envs/psi4-env）
  - 脚本以 psi4-env 的 python 运行（psi4 作为模块 import），stdout 里
    '@@PROGRESS@@ ...' 行实时回传阶段提示，result.json 承载结构化结果
  - 超时默认 1800s（COF_DFT_TIMEOUT_PSI4 覆盖），超时/非零退出 → DftError 中文原因
  - fchk 由 psi4 fchk writer 写出，归档到 user_data_root()/dft_artifacts/
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

try:
    from src import runtime_config
except ImportError:  # pragma: no cover - 直接以 src 为 sys.path 运行时
    import runtime_config  # type: ignore

try:
    from src.dft import dimer as dimer_mod
    from src.dft import engine
    DimerError = dimer_mod.DimerError
    DftError = engine.DftError
except ImportError:  # pragma: no cover
    from dft import dimer as dimer_mod  # type: ignore
    from dft import engine  # type: ignore
    DimerError = dimer_mod.DimerError
    DftError = engine.DftError

logger = logging.getLogger(__name__)

HARTREE_TO_KCAL = engine.HARTREE_TO_KCAL
HARTREE_TO_KJ = engine.HARTREE_TO_KJ
HARTREE_TO_EV = 27.211386

# Psi4 方法档位（method key → psi4 调用名/基组/中文标签）
PSI4_METHODS: dict[str, dict] = {
    "wb97xd3bj_svp": {
        "psi4_name": "wb97x-d3bj",
        "basis": "def2-svp",
        "label": "ωB97X-D3BJ/def2-SVP（真 DFT）",
        "preset": "precision",
        "e_convergence": 1e-6,
    },
    "wb97xd3bj_svp_quick": {
        "psi4_name": "wb97x-d3bj",
        "basis": "def2-SV(P)",
        "label": "ωB97X-D3BJ/def2-SV(P)（批量快速档）",
        "preset": "batch",
        "e_convergence": 3e-6,
    },
    "b3lyp_631gdp": {
        "psi4_name": "b3lyp",
        "basis": "6-31g(d,p)",
        "label": "B3LYP/6-31G(d,p)（文献口径）",
        "preset": "literature",
        "e_convergence": 1e-6,
    },
}
DEFAULT_PSI4_METHOD = "wb97xd3bj_svp"

# preset 别名 → method key（对齐 COF 文献常用口径：precision=高精度泛函，
# literature=B3LYP/6-31G(d,p)，如刘璐 2021 J. Hazard. Mater. 403, 123917）
PSI4_PRESET_ALIASES = {"precision": "wb97xd3bj_svp",
                       "literature": "b3lyp_631gdp"}


def resolve_method_key(method: str) -> str:
    """preset 别名（precision/literature）→ 方法档 key；非别名原样返回。"""
    return PSI4_PRESET_ALIASES.get((method or "").strip(), method)
DEFAULT_TIMEOUT = 1800  # 真 DFT 分钟级：默认 30 分钟

PROGRESS_PREFIX = "@@PROGRESS@@"

INSTALL_HINT = (
    "未安装 Psi4 精度档环境（psi4-env）。请运行 scripts/install_psi4_env.bat"
    " 一键安装（conda create -n psi4-env -c conda-forge psi4 dftd3-python"
    " python=3.11，约 300MB+ 下载）；装在非常规位置时请把解释器路径填入"
    " config/runtime.local.json 的 pythons.psi4 或设环境变量 COF_PSI4_PYTHON。")


class Psi4NotInstalledError(DftError):
    """psi4-env 未安装/不可用的专属异常（API 层据此给 503 + 安装引导）。"""


def psi4_timeout(n_atoms: int | None = None) -> int:
    """精度档超时秒数（环境变量 COF_DFT_TIMEOUT_PSI4 可覆盖）。

    未覆盖时按复合物原子数自适应：默认 1800s 对 >50 原子的大体系偏紧
    （基准实测 89 原子 CP 单点需约 67 min），故分档放宽。
    """
    env = os.environ.get("COF_DFT_TIMEOUT_PSI4", "").strip()
    if env.isdigit():
        return int(env)
    if n_atoms is None or n_atoms <= 50:
        return DEFAULT_TIMEOUT
    if n_atoms <= 90:
        return 3600
    return 5400


# ---------------------------------------------------------------- 环境检测

def detect_psi4(timeout: int = 90) -> dict:
    """检测 psi4-env 可用性。

    Returns:
        {"installed": bool, "version": str|None, "path": str|None, "reason": str}
    """
    py = runtime_config.psi4_python()
    if py is None:
        return {"installed": False, "version": None, "path": None,
                "reason": INSTALL_HINT}
    try:
        proc = subprocess.run(
            [str(py), "-c",
             "import psi4, dftd3; print(psi4.__version__)"],
            capture_output=True, text=True, timeout=timeout, errors="replace",
            encoding="utf-8",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
            if sys.platform == "win32" else 0)
    except subprocess.TimeoutExpired:
        return {"installed": False, "version": None, "path": str(py),
                "reason": f"psi4-env 解释器存在但 import psi4 超时（{timeout}s），"
                          "环境可能损坏，建议重跑 scripts/install_psi4_env.bat"}
    except OSError as exc:
        return {"installed": False, "version": None, "path": str(py),
                "reason": f"psi4-env 解释器无法执行：{exc}"}
    if proc.returncode != 0:
        return {"installed": False, "version": None, "path": str(py),
                "reason": f"psi4-env 中 import psi4 失败："
                          f"{(proc.stderr or proc.stdout or '').strip()[:200]}"}
    version = (proc.stdout or "").strip().splitlines()
    return {"installed": True, "version": version[-1] if version else None,
            "path": str(py), "reason": "ok"}


# ---------------------------------------------------------------- 输入生成

def _xyz_atoms(xyz_block: str) -> list[tuple[str, float, float, float]]:
    """xyz 文本 → [(元素, x, y, z), ...]（跳过首两行表头）。"""
    lines = [ln for ln in xyz_block.strip().splitlines() if ln.strip()]
    atoms = []
    for ln in lines[2:]:
        parts = ln.split()
        if len(parts) < 4:
            continue
        try:
            atoms.append((parts[0], float(parts[1]), float(parts[2]),
                          float(parts[3])))
        except ValueError:
            continue
    return atoms


def generate_psi4_script(complex_xyz: str, n_frag_a: int,
                         method_key: str = DEFAULT_PSI4_METHOD,
                         *, optimize: bool = True, same_fragments: bool = False,
                         threads: int = 4, memory_mb: int = 6000,
                         e_convergence: float | None = None) -> str:
    """生成 psi4-env 子进程要跑的 Python 脚本（计算逻辑全在脚本内，结果落 result.json）。

    Args:
        complex_xyz: 复合物初始几何（先主体后客体拼接，原子序区间不变）
        n_frag_a: 片段 A（二聚体/分子 A）的原子数——psi4 '--' 分片段依据
        method_key: PSI4_METHODS 键
        optimize: True 时先做 psi4 几何优化（初猜已是 xTB 预优化几何）
        threads/memory_mb: psi4 并行与内存上限
        e_convergence: SCF 能量收敛阈值（缺省取方法档 spec，默认 1e-6）

    脚本产物（cwd 下）：result.json（结构化结果）、complex_opt.xyz（优化后几何，
    表头第二行注释带 fragment 边界）、complex.fchk（Gaussian 格式检查点）。
    """
    spec = PSI4_METHODS.get(resolve_method_key(method_key))
    if spec is None:
        raise DftError(f"未知的 Psi4 方法档位：{method_key}"
                       f"（可选 {' / '.join(PSI4_METHODS)}）")
    atoms = _xyz_atoms(complex_xyz)
    if not atoms:
        raise DftError("复合物几何为空，无法生成 Psi4 输入")
    if not 0 < n_frag_a < len(atoms):
        raise DftError(
            f"片段边界非法：片段 A 原子数 {n_frag_a}，复合物共 {len(atoms)} 个原子")

    e_conv = e_convergence if e_convergence is not None \
        else spec.get("e_convergence", 1e-6)
    atoms_json = json.dumps(atoms)
    return _SCRIPT_TEMPLATE.format(
        atoms_json=atoms_json,
        n_frag_a=n_frag_a,
        psi4_name=spec["psi4_name"],
        basis=spec["basis"],
        label=spec["label"],
        optimize_flag="True" if optimize else "False",
        same_fragments="True" if same_fragments else "False",
        threads=threads,
        memory_mb=memory_mb,
        e_convergence=e_conv,
        progress_prefix=PROGRESS_PREFIX,
    )


# 脚本模板：在 psi4-env 的 python 中运行。仅依赖 psi4 + 标准库 + numpy（psi4 自带）。
_SCRIPT_TEMPLATE = '''# -*- coding: utf-8 -*-
"""COF 科研助手生成的 Psi4 精度档计算脚本（自动生成，勿手改）。

阶段：{label}
  1.（可选）几何优化（初猜为 xTB 预优化几何）
  2. counterpoise (BSSE) 校正结合能：E_cp = E(AB@AB) - E(A@AB) - E(B@AB)
  3. 片段各自单点能（本基组，供能量分解表展示；非 CP 参考结合能由此算）
  4. 复合物 SCF + wfn：HOMO-LUMO gap、偶极矩、fchk 写出
产物：result.json / complex_opt.xyz / complex.fchk；进度走 stdout '@@PROGRESS@@' 行。
"""
import json
import traceback

# --- qcengine/py-cpuinfo 绕过（Windows 11 移除 wmic 后 py-cpuinfo 在 import 时
# CPU arch 检测即崩溃，一切 qcengine 路由的计算——nbody counterpoise、dftd3
# 色散——都会失败。塞入一个假的 cpuinfo 模块到 sys.modules，仅提供
# get_cpu_info() 桩；qcengine 只用它取 brand 展示，不影响计算）
try:
    import sys as _sys
    import types as _types
    _fake_cpuinfo = _types.ModuleType("cpuinfo")
    _fake_cpuinfo.get_cpu_info = lambda: {{"brand_raw": "unknown", "brand": "unknown"}}
    _sys.modules["cpuinfo"] = _fake_cpuinfo
except Exception:
    pass

import psi4

PROGRESS = "{progress_prefix}"
HARTREE_TO_EV = 27.211386
AU_TO_DEBYE = 2.541746


def progress(msg):
    print(PROGRESS + " " + msg, flush=True)


def mol_string(atoms_a, atoms_b=None):
    lines = ["0 1"]
    lines += ["{{}} {{:.10f}} {{:.10f}} {{:.10f}}".format(*a) for a in atoms_a]
    if atoms_b is not None:
        lines.append("--")
        lines.append("0 1")
        lines += ["{{}} {{:.10f}} {{:.10f}} {{:.10f}}".format(*a) for a in atoms_b]
    lines.append("units angstrom")
    lines.append("symmetry c1")  # 固定 C1：保持原子输入顺序，fragment 区间不失效
    return "\\n".join(lines)


def main():
    atoms = {atoms_json}
    n_a = {n_frag_a}
    atoms_a, atoms_b = atoms[:n_a], atoms[n_a:]
    method = "{psi4_name}/{basis}"

    psi4.set_memory("{memory_mb} MB")
    psi4.set_num_threads({threads})
    psi4.core.set_output_file("psi4_output.dat", False)
    psi4.set_options({{
        "basis": "{basis}",
        "scf_type": "df",
        "reference": "rks",
        "guess": "sad",
        "e_convergence": {e_convergence},
    }})

    result = {{"method": "{psi4_name}", "basis": "{basis}",
              "label": "{label}", "bsse_type": "cp",
              "psi4_version": psi4.__version__}}

    mol = psi4.geometry(mol_string(atoms_a, atoms_b))
    progress("Psi4 初始化完成（psi4 {{}}，{{}} 个原子，基组 {basis}）".format(
        psi4.__version__, mol.natom()))

    if {optimize_flag}:
        progress("几何优化中（{label}，初猜来自 xTB 预优化）…")
        psi4.optimize(method, molecule=mol)
        progress("几何优化完成")
    mol.save_xyz_file("complex_opt.xyz", True)

    progress("结合能计算中（counterpoise BSSE 校正）…")
    e_cp = psi4.energy(method, bsse_type="cp", molecule=mol)
    result["e_bind_cp_hartree"] = float(e_cp)

    progress("片段单点能计算中（能量分解用）…")
    mol_a = psi4.geometry(mol_string(atoms_a))
    e_a = psi4.energy(method, molecule=mol_a)
    if {same_fragments}:
        e_b = e_a
    else:
        mol_b = psi4.geometry(mol_string(atoms_b))
        e_b = psi4.energy(method, molecule=mol_b)
    result["e_monomer_a_hartree"] = float(e_a)
    result["e_monomer_b_hartree"] = float(e_b)

    progress("复合物性质计算中（HOMO-LUMO gap / 偶极矩 / fchk）…")
    e_cplx, wfn = psi4.energy(method, molecule=mol, return_wfn=True)
    result["e_complex_hartree"] = float(e_cplx)
    result["e_bind_raw_hartree"] = float(e_cplx - e_a - e_b)

    # HOMO-LUMO gap（eV；RKS 下取 alpha 套轨道）
    try:
        homo = int(wfn.nalpha()) - 1
        eps = wfn.epsilon_a()
        try:
            import numpy as np
            arr = np.asarray(eps.np)  # psi4.core.Vector → numpy（C1 单不可约表示）
        except Exception:
            arr = [eps.get(0, i) for i in range(eps.dim(0))]
        if 0 <= homo and homo + 1 < len(arr):
            result["gap_ev_complex"] = float(
                (arr[homo + 1] - arr[homo]) * HARTREE_TO_EV)
        else:
            result["gap_ev_complex"] = None
    except Exception:
        result["gap_ev_complex"] = None

    # 偶极矩（Debye）
    def _vec3(v):
        try:
            return float(v[0]), float(v[1]), float(v[2])
        except Exception:
            return float(v.get(0)), float(v.get(1)), float(v.get(2))

    try:
        dx, dy, dz = _vec3(wfn.variable("SCF DIPOLE"))
        result["dipole_debye_complex"] = float(
            (dx * dx + dy * dy + dz * dz) ** 0.5 * AU_TO_DEBYE)
    except Exception:
        try:
            dx, dy, dz = _vec3(wfn.variable("CURRENT DIPOLE"))
            result["dipole_debye_complex"] = float(
                (dx * dx + dy * dy + dz * dz) ** 0.5 * AU_TO_DEBYE)
        except Exception:
            result["dipole_debye_complex"] = None

    # fchk（Gaussian 工作流对接）
    try:
        psi4.fchk(wfn, "complex.fchk")
        result["fchk_written"] = True
    except Exception as exc:
        result["fchk_written"] = False
        result["fchk_error"] = str(exc)[:200]

    progress("正在写出结果…")
    with open("result.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)
    progress("计算完成")


try:
    main()
except Exception:
    traceback.print_exc()
    with open("result.json", "w", encoding="utf-8") as f:
        json.dump({{"error": traceback.format_exc()[-1500:]}}, f,
                  ensure_ascii=False)
    raise SystemExit(1)
'''


# ---------------------------------------------------------------- 子进程调用

def _read_xyz_with_atoms(path: Path) -> str | None:
    """读 psi4 save_xyz_file 产物；失败返回 None。"""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None


def _run_psi4_script(script_text: str, cwd: Path, timeout: int,
                     on_stage=None) -> dict:
    """在 cwd 下用 psi4-env python 跑生成的脚本，返回 result.json 内容。

    stdout 中 '@@PROGRESS@@ <msg>' 行实时触发 on_stage(msg)；超时强杀。

    Raises:
        Psi4NotInstalledError: psi4-env 不可用
        DftError: 超时 / 非零退出 / result.json 缺失或带 error
    """
    det = detect_psi4()
    if not det["installed"]:
        raise Psi4NotInstalledError(det["reason"])
    py = det["path"]

    cwd.mkdir(parents=True, exist_ok=True)
    script_path = cwd / "run_psi4.py"
    script_path.write_text(script_text, encoding="utf-8")

    env = os.environ.copy()
    env.setdefault("OMP_NUM_THREADS", "4")
    # PSI_SCRATCH 产品化：Psi4 运行时产生 GB 级临时文件，目录经
    # runtime_config 配置链解析（COF_PSI4_SCRATCH > runtime.local.json
    # psi4_scratch > E:\psi4_scratch 探测 > user_data_root()/psi4_scratch）
    try:
        scratch = runtime_config.psi4_scratch_dir()
        scratch.mkdir(parents=True, exist_ok=True)
        env["PSI_SCRATCH"] = str(scratch)
    except Exception as exc:  # 目录不可写等异常不阻断计算（psi4 用默认临时目录）
        logger.warning("PSI_SCRATCH 目录准备失败，退回系统默认临时目录: %s", exc)
    env["PYTHONIOENCODING"] = "utf-8"  # 子进程 stdout/stderr 统一 UTF-8（进度行为中文）
    # UTF-8 模式：psi4 schema_wrapper 等用 locale 默认编码读输出文件，
    # 中文 Windows（cp936）下会对 psi4 输出中的非 ASCII 字符炸 UnicodeDecodeError
    env["PYTHONUTF8"] = "1"
    # psi4-env 的 Library/bin 入 PATH：s-dftd3.exe（simple-dftd3 包，D3BJ 色散
    # 校正必需）装在这里，qcengine 按 PATH 探测外部程序
    psi4_env_dir = str(Path(py).parent)
    extra_paths = [psi4_env_dir,
                   str(Path(psi4_env_dir) / "Library" / "bin"),
                   str(Path(psi4_env_dir) / "Scripts")]
    env["PATH"] = os.pathsep.join(extra_paths + [env.get("PATH", "")])

    def stage(msg: str) -> None:
        if on_stage is not None:
            try:
                on_stage(msg)
            except Exception:
                pass

    stage("正在启动 Psi4 子进程…")
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) \
        if sys.platform == "win32" else 0
    try:
        proc = subprocess.Popen(
            [py, "run_psi4.py"], cwd=str(cwd), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace", creationflags=creationflags)
    except FileNotFoundError:
        raise Psi4NotInstalledError(INSTALL_HINT)
    except OSError as exc:
        raise DftError(f"Psi4 子进程启动失败：{exc}")

    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    def _reader(stream, sink, is_stdout: bool) -> None:
        try:
            for line in iter(stream.readline, ""):
                sink.append(line)
                if is_stdout and line.startswith(PROGRESS_PREFIX):
                    stage(line[len(PROGRESS_PREFIX):].strip())
        except Exception:
            pass
        finally:
            try:
                stream.close()
            except Exception:
                pass

    t_out = threading.Thread(target=_reader,
                             args=(proc.stdout, stdout_lines, True), daemon=True)
    t_err = threading.Thread(target=_reader,
                             args=(proc.stderr, stderr_lines, False), daemon=True)
    t_out.start()
    t_err.start()

    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=10)
        except Exception:
            pass
        raise DftError(
            f"Psi4 计算超时（超过 {timeout} 秒仍未完成）：体系过大或优化不收敛，"
            "可设环境变量 COF_DFT_TIMEOUT_PSI4 放宽超时，或改用 xTB 快速档")
    finally:
        t_out.join(timeout=5)
        t_err.join(timeout=5)

    stderr_tail = "".join(stderr_lines)[-800:].strip()
    result_path = cwd / "result.json"
    data: dict = {}
    if result_path.is_file():
        try:
            data = json.loads(result_path.read_text(encoding="utf-8"))
        except Exception:
            data = {}

    if proc.returncode != 0 or "error" in data:
        detail = (data.get("error") or stderr_tail or "未知错误").strip()
        # traceback 最后一行通常最能说明问题
        last = detail.splitlines()[-1] if detail else "未知错误"
        raise DftError(f"Psi4 计算失败：{last[:300]}")
    if not data:
        raise DftError("Psi4 子进程未产出结果文件（result.json 缺失或损坏），"
                       f"stderr 摘要：{stderr_tail[:300] or '（空）'}")
    return data


# ---------------------------------------------------------------- 输出解析

def parse_psi4_result(data: dict) -> dict:
    """result.json → 标准化字段（能量换算 kcal/kJ，缺失字段给 None）。"""
    e_cp = data.get("e_bind_cp_hartree")
    if e_cp is None:
        raise DftError("Psi4 结果中缺少 counterpoise 结合能，计算不完整")
    e_raw = data.get("e_bind_raw_hartree")
    return {
        "e_bind_hartree": float(e_cp),
        "e_bind_kcal": float(e_cp) * HARTREE_TO_KCAL,
        "e_bind_kj": float(e_cp) * HARTREE_TO_KJ,
        "e_bind_raw_hartree": None if e_raw is None else float(e_raw),
        "e_bind_raw_kcal": None if e_raw is None else float(e_raw) * HARTREE_TO_KCAL,
        "energies_hartree": {
            "dimer": data.get("e_monomer_a_hartree"),
            "x": data.get("e_monomer_b_hartree"),
            "complex": data.get("e_complex_hartree"),
        },
        "gap_ev_complex": data.get("gap_ev_complex"),
        "dipole_debye_complex": data.get("dipole_debye_complex"),
        "fchk_written": bool(data.get("fchk_written")),
        "psi4_version": data.get("psi4_version"),
    }


# ---------------------------------------------------------------- 主管线

def _xtb_guess(xyz_c: str, work_dir: Path, on_stage) -> str:
    """xTB 预优化复合物初猜；xtb 不可用或失败时退化为原 UFF 初猜。"""
    if engine.xtb_binary() is None:
        on_stage("未找到 xTB，跳过预优化（以力场初猜直接提交 Psi4）…")
        return xyz_c
    try:
        on_stage("正在用 xTB 预优化复合物初猜（Psi4 初始几何）…")
        _, opt_xyz = engine._run_xtb(
            xyz_c, engine.METHODS["gfn2"]["args"], work_dir,
            engine.method_timeout("gfn2"))
        return opt_xyz or xyz_c
    except DftError as exc:
        logger.warning("xTB 预优化失败，退化为 UFF 初猜: %s", exc)
        on_stage("xTB 预优化未成功，以力场初猜直接提交 Psi4…")
        return xyz_c


def _finalize(common: dict, parsed: dict, opt_xyz: str, fchk_src: Path | None,
              artifacts_dir: Path, tag: str, method_key: str,
              started: float) -> dict:
    """把 parse_psi4_result 的字段合入引擎口径的结果 dict，并归档 fchk。"""
    spec = PSI4_METHODS[method_key]
    fchk_rel = None
    if parsed["fchk_written"] and fchk_src is not None and fchk_src.is_file():
        try:
            artifacts_dir.mkdir(parents=True, exist_ok=True)
            dst = artifacts_dir / f"{tag}.fchk"
            dst.write_bytes(fchk_src.read_bytes())
            fchk_rel = str(dst)
        except Exception as exc:
            logger.warning("fchk 归档失败: %s", exc)

    common.update({
        "backend": "psi4",
        "method": method_key,
        "method_label": spec["label"],
        "e_bind_hartree": parsed["e_bind_hartree"],
        "e_bind_kcal": parsed["e_bind_kcal"],
        "e_bind_kj": parsed["e_bind_kj"],
        "energies_hartree": parsed["energies_hartree"],
        # 片段 gap/偶极矩未单独算（省两次 SCF），仅复合物有值
        "gap_ev": {"dimer": None, "x": None,
                   "complex": parsed["gap_ev_complex"]},
        "dipole_debye": {"dimer": None, "x": None,
                         "complex": parsed["dipole_debye_complex"]},
        "complex_xyz": opt_xyz,
        "elapsed_sec": round(time.time() - started, 2),
        "psi4_detail": {
            "method": spec["psi4_name"],
            "basis": spec["basis"],
            "bsse_type": "cp",
            "psi4_version": parsed["psi4_version"],
            "e_bind_raw_kcal": parsed["e_bind_raw_kcal"],
            "fchk_available": fchk_rel is not None,
            "fchk_path": fchk_rel,
        },
    })
    return common


def compute_binding_psi4(ald_smiles: str, amine_smiles: str,
                         method: str = DEFAULT_PSI4_METHOD,
                         x_type: str = "self_stack",
                         solvent_id: str | None = None,
                         ald2_smiles: str | None = None,
                         amine2_smiles: str | None = None,
                         custom_smiles: str | None = None,
                         on_stage=None, jobs_root: Path | None = None,
                         optimize: bool = False,
                         complex_xyz: str | None = None,
                         n_samples: int | None = None,
                         threads: int | None = None) -> dict:
    """「缩合二聚体 D 与第三物质 X」结合能的 Psi4 精度档实现。

    参数与返回值口径对齐 engine.compute_binding，多带 backend="psi4" 与
    psi4_detail（方法/基组/BSSE 口径/未校正结合能/fchk 路径）。
    method 支持 preset 别名：precision（默认 ωB97X-D3BJ/def2-SVP）/
    literature（B3LYP/6-31G(d,p)）/ batch（ωB97X-D3BJ/def2-SV(P) 批量快速档）。
    complex_xyz：可选，外部提供的 D·X 复合物初猜 xyz（如经 xTB 取向筛选后的
    几何或构象采样产物）；提供时跳过取向采样/初猜生成。n_samples：MC 取向采样数
    optimize：默认 False——初猜已是 xTB 预优化几何，直接单点 CP（大幅提速且
    S66 验证误差达标）；显式 True 才做 Psi4 全优化。
    threads：并行线程数；None 时取 runtime_config.psi4_threads()。

    Raises:
        Psi4NotInstalledError: psi4-env 未安装
        DftError: 任何一步失败（message 为中文原因）
    """
    method = resolve_method_key(method)
    if method not in PSI4_METHODS:
        raise DftError(f"未知的 Psi4 方法档位：{method}"
                       f"（可选 {' / '.join(PSI4_METHODS)}"
                       "，或 preset 别名 precision / literature）")

    canon_ald = engine.canonicalize_smiles(ald_smiles)
    canon_amine = engine.canonicalize_smiles(amine_smiles)
    if canon_ald is None:
        raise DftError(f"醛单体的 SMILES 无法解析：{(ald_smiles or '')[:80]}")
    if canon_amine is None:
        raise DftError(f"胺单体的 SMILES 无法解析：{(amine_smiles or '')[:80]}")

    det = detect_psi4()
    if not det["installed"]:
        raise Psi4NotInstalledError(det["reason"])

    def stage(hint: str) -> None:
        if on_stage is not None:
            try:
                on_stage(hint)
            except Exception:
                pass

    stage("正在生成缩合二聚体（醛 + 胺 → 亚胺）…")
    try:
        dim = dimer_mod.make_dimer(canon_ald, canon_amine)
    except DimerError as exc:
        raise DftError(f"二聚体生成失败：{exc}")
    dimer_smiles = dim["smiles"]

    x_smiles, x_description, x_cache_part = engine.resolve_x(
        x_type, dimer_smiles, solvent_id=solvent_id,
        ald2_smiles=ald2_smiles, amine2_smiles=amine2_smiles,
        custom_smiles=custom_smiles)

    if jobs_root is None:
        jobs_root = runtime_config.user_data_root() / "dft_jobs"
    tag = hashlib.sha1(
        f"psi4|{dimer_smiles}|{x_cache_part}|{method}|{time.time_ns()}".encode()
    ).hexdigest()[:12]
    job_dir = Path(tempfile.mkdtemp(prefix=f"dft_psi4_{tag}_",
                                    dir=_ensure_dir(jobs_root)))
    started = time.time()

    try:
        stage("正在构造 D·X 复合物初猜…")
        xyz_d = engine.embed_monomer_xyz(dimer_smiles)
        sampling = None
        if complex_xyz is not None:
            xyz_c = complex_xyz
        elif engine.xtb_binary() is not None and n_samples != 1:
            # MC 取向采样 + xTB 分级筛选（对标文献 Monte Carlo 吸附构象采样）
            info = engine.screen_complex_xtb(dimer_smiles, x_smiles,
                                             job_dir / "screen",
                                             n_samples=n_samples,
                                             on_stage=stage)
            xyz_c = info["best_xyz"]
            sampling = {"n_samples": info["n_samples"],
                        "best_kind": info["best_kind"],
                        "screen_level": info["screen_level"]}
        else:
            xyz_c = engine.embed_complex_xyz(dimer_smiles, x_smiles)
        n_a = engine._xyz_atom_count(xyz_d)
        n_atoms = engine._xyz_atom_count(xyz_c)
        if n_atoms > engine.LARGE_SYSTEM_ATOMS:
            stage(f"体系较大（复合物 {n_atoms} 个原子），Psi4 真 DFT 预计耗时"
                  "较长，请耐心等待…")

        guess = _xtb_guess(xyz_c, job_dir / "xtb_guess", stage)

        stage("正在生成 Psi4 输入脚本…")
        script = generate_psi4_script(guess, n_a, method, optimize=optimize,
                                      same_fragments=(x_smiles == dimer_smiles),
                                      threads=(threads or runtime_config.psi4_threads()))
        run_dir = job_dir / "psi4"
        data = _run_psi4_script(script, run_dir, psi4_timeout(n_atoms),
                                on_stage=stage)

        stage("正在解析计算结果…")
        parsed = parse_psi4_result(data)
        opt_xyz = _read_xyz_with_atoms(run_dir / "complex_opt.xyz") or guess

        common = {
            "mode": "dimer",
            "smiles_a": canon_ald,
            "smiles_b": canon_amine,
            "dimer_smiles": dimer_smiles,
            "dimer_multi_site": bool(dim.get("multi_site")),
            "dimer_note": dim.get("note"),
            "x_type": x_type,
            "x_smiles": x_smiles,
            "x_description": x_description,
            "x_cache_part": x_cache_part,
            "x_request": {
                "solvent_id": solvent_id if x_type == "solvent" else None,
                "ald2_smiles": ald2_smiles if x_type == "other_dimer" else None,
                "amine2_smiles": amine2_smiles if x_type == "other_dimer" else None,
                "custom_smiles": custom_smiles if x_type == "custom" else None,
            },
            "complex_atom_count": n_atoms,
            # 原子计数口径：复合物 = 二聚体 + X；self_stack 时 X=二聚体自身 → complex = 2×dimer
            "atom_budget": {"dimer": n_a, "x": n_atoms - n_a, "complex": n_atoms},
            "fragment_ranges": {"a": [0, n_a], "b": [n_a, n_atoms]},
            "sampling": sampling,
        }
        return _finalize(
            common, parsed, opt_xyz, run_dir / "complex.fchk",
            runtime_config.user_data_root() / "dft_artifacts", tag, method,
            started)
    finally:
        import shutil
        shutil.rmtree(job_dir, ignore_errors=True)


def compute_pair_binding_psi4(smiles_a: str, smiles_b: str,
                              method: str = DEFAULT_PSI4_METHOD,
                              on_stage=None, jobs_root: Path | None = None,
                              optimize: bool = False,
                              complex_xyz: str | None = None,
                              n_samples: int | None = None,
                              threads: int | None = None) -> dict:
    """任意双分子 A···B 结合能的 Psi4 精度档实现（对齐 engine.compute_pair_binding）。

    method 支持 preset 别名：precision（默认 ωB97X-D3BJ/def2-SVP）/
    literature（B3LYP/6-31G(d,p)）/ batch（ωB97X-D3BJ/def2-SV(P) 批量快速档）。
    complex_xyz：可选，外部提供的 A···B 复合物初猜 xyz（如经 xTB 取向筛选后的
    几何）；提供时跳过取向采样/初猜生成。原子顺序须为 A 片段在前、B 片段
    在后。n_samples：MC 取向采样数（None=默认/环境变量；1=旧单取向初猜）。
    optimize：默认 False（初猜已是 xTB 预优化几何，直接单点 CP 提速）；
    threads：并行线程数；None 时取 runtime_config.psi4_threads()。

    Raises:
        Psi4NotInstalledError / DftError（中文原因）
    """
    method = resolve_method_key(method)
    if method not in PSI4_METHODS:
        raise DftError(f"未知的 Psi4 方法档位：{method}"
                       f"（可选 {' / '.join(PSI4_METHODS)}"
                       "，或 preset 别名 precision / literature）")

    canon_a = engine.canonicalize_smiles(smiles_a)
    canon_b = engine.canonicalize_smiles(smiles_b)
    if canon_a is None:
        raise DftError(f"分子 A 的 SMILES 无法解析：{(smiles_a or '')[:80]}")
    if canon_b is None:
        raise DftError(f"分子 B 的 SMILES 无法解析：{(smiles_b or '')[:80]}")

    det = detect_psi4()
    if not det["installed"]:
        raise Psi4NotInstalledError(det["reason"])

    def stage(hint: str) -> None:
        if on_stage is not None:
            try:
                on_stage(hint)
            except Exception:
                pass

    if jobs_root is None:
        jobs_root = runtime_config.user_data_root() / "dft_jobs"
    tag = hashlib.sha1(
        f"psi4|pair|{canon_a}|{canon_b}|{method}|{time.time_ns()}".encode()
    ).hexdigest()[:12]
    job_dir = Path(tempfile.mkdtemp(prefix=f"dft_psi4_{tag}_",
                                    dir=_ensure_dir(jobs_root)))
    started = time.time()

    try:
        stage("正在构造 A···B 复合物初猜…")
        xyz_a = engine.embed_monomer_xyz(canon_a)
        sampling = None
        if complex_xyz is not None:
            xyz_c = complex_xyz
        elif engine.xtb_binary() is not None and n_samples != 1:
            info = engine.screen_complex_xtb(canon_a, canon_b,
                                             job_dir / "screen",
                                             n_samples=n_samples,
                                             on_stage=stage)
            xyz_c = info["best_xyz"]
            sampling = {"n_samples": info["n_samples"],
                        "best_kind": info["best_kind"],
                        "screen_level": info["screen_level"]}
        else:
            xyz_c = engine.embed_complex_xyz(canon_a, canon_b)
        n_a = engine._xyz_atom_count(xyz_a)
        n_atoms = engine._xyz_atom_count(xyz_c)
        if n_atoms > engine.LARGE_SYSTEM_ATOMS:
            stage(f"体系较大（复合物 {n_atoms} 个原子），Psi4 真 DFT 预计耗时"
                  "较长，请耐心等待…")

        guess = _xtb_guess(xyz_c, job_dir / "xtb_guess", stage)

        stage("正在生成 Psi4 输入脚本…")
        script = generate_psi4_script(guess, n_a, method, optimize=optimize,
                                      same_fragments=(canon_b == canon_a),
                                      threads=(threads or runtime_config.psi4_threads()))
        run_dir = job_dir / "psi4"
        data = _run_psi4_script(script, run_dir, psi4_timeout(n_atoms),
                                on_stage=stage)

        stage("正在解析计算结果…")
        parsed = parse_psi4_result(data)
        opt_xyz = _read_xyz_with_atoms(run_dir / "complex_opt.xyz") or guess

        common = {
            "mode": "pair",
            "smiles_a": canon_a,
            "smiles_b": canon_b,
            "dimer_smiles": None,
            "dimer_multi_site": False,
            "dimer_note": None,
            "x_type": None,
            "x_smiles": canon_b,
            "x_description": engine.PAIR_X_DESCRIPTION,
            "x_cache_part": f"pair:{canon_b}",
            "x_request": {"solvent_id": None, "ald2_smiles": None,
                          "amine2_smiles": None, "custom_smiles": None},
            "complex_atom_count": n_atoms,
            # 原子计数口径：复合物 = 二聚体 + X；self_stack 时 X=二聚体自身 → complex = 2×dimer
            "atom_budget": {"dimer": n_a, "x": n_atoms - n_a, "complex": n_atoms},
            "fragment_ranges": {"a": [0, n_a], "b": [n_a, n_atoms]},
            "sampling": sampling,
        }
        return _finalize(
            common, parsed, opt_xyz, run_dir / "complex.fchk",
            runtime_config.user_data_root() / "dft_artifacts", tag, method,
            started)
    finally:
        import shutil
        shutil.rmtree(job_dir, ignore_errors=True)


def _ensure_dir(p: Path) -> str:
    p.mkdir(parents=True, exist_ok=True)
    return str(p)
