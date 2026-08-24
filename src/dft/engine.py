"""xTB 半经验计算引擎：两单体结合能 + gap/偶极矩解析。

管线（方案 §3.3）：
    SMILES → RDKit ETKDG 3D + UFF 预优化 → xyz
           → xtb --opt（GFN-FF 或 GFN2-xTB）
           → E_bind = E_复合物 − E_A − E_B（hartree → kcal/mol、 kJ/mol）

复合物初猜复用 dimer.py 思路：两单体 CombineMols 后 ETKDGv3 多构象嵌入
（非键相互作用会把两单体放在合理相对位置），取力场能量最低的构象。

子进程约定：
  - 二进制在 resource_root()/vendor/xtb/bin/xtb.exe（frozen 时在 _MEIPASS 下）
  - 必须注入 XTBPATH=<vendor>/xtb/share/xtb，cwd 为任务临时目录
  - 超时：gfnff 60s / gfn2 300s（COF_DFT_TIMEOUT_GFNFF / COF_DFT_TIMEOUT_GFN2 可覆盖）
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from rdkit import Chem
from rdkit.Chem import AllChem

try:
    from src import runtime_config
except ImportError:  # pragma: no cover - 直接以 src 为 sys.path 运行时
    import runtime_config  # type: ignore

logger = logging.getLogger(__name__)

HARTREE_TO_KCAL = 627.509
HARTREE_TO_KJ = 2625.5

# 方法档位：命令行参数 + 默认超时（秒）
METHODS: dict[str, dict] = {
    "gfnff": {"args": ["--gfnff"], "label": "GFN-FF 力场（快速）"},
    "gfn2": {"args": ["--gfn", "2"], "label": "GFN2-xTB（精确）"},
}
DEFAULT_TIMEOUT = {"gfnff": 60, "gfn2": 300}

_XTB_MAX_Z = 86  # GFN2-xTB / GFN-FF 参数化覆盖到 Rn


class DftError(Exception):
    """计算失败的统一异常，message 为面向用户的中文原因。"""


def method_timeout(method: str) -> int:
    """方法档位的超时秒数（环境变量可覆盖）。"""
    env = os.environ.get(f"COF_DFT_TIMEOUT_{method.upper()}", "").strip()
    if env.isdigit():
        return int(env)
    return DEFAULT_TIMEOUT.get(method, 300)


def xtb_binary() -> Path | None:
    """定位 xtb 可执行文件；找不到返回 None（调用方降级中文报错）。"""
    exe = runtime_config.resource_root() / "vendor" / "xtb" / "bin" / "xtb.exe"
    return exe if exe.is_file() else None


def xtb_share_dir() -> Path:
    return runtime_config.resource_root() / "vendor" / "xtb" / "share" / "xtb"


def canonicalize_smiles(smiles: str) -> str | None:
    """RDKit 规范化 SMILES；无法解析返回 None。"""
    mol = Chem.MolFromSmiles(smiles.strip()) if smiles and smiles.strip() else None
    if mol is None:
        return None
    return Chem.MolToSmiles(mol)


# ---------------------------------------------------------------- 输出解析

_RE_ENERGY = re.compile(
    r"\|\s*TOTAL ENERGY\s+(-?\d+\.\d+(?:[eE][-+]?\d+)?)\s+Eh")
_RE_GAP = re.compile(r"HOMO-LUMO GAP\s+(\d+\.\d+(?:[eE][-+]?\d+)?)\s*eV")
_RE_DIPOLE_FULL = re.compile(
    r"full:\s+[-\d.eE+]+\s+[-\d.eE+]+\s+[-\d.eE+]+\s+([\d.eE+-]+)")


def parse_energy(stdout: str) -> float | None:
    """从 xtb 输出抓 TOTAL ENERGY（hartree）。"""
    m = _RE_ENERGY.search(stdout)
    return float(m.group(1)) if m else None


def parse_gap_ev(stdout: str) -> float | None:
    """HOMO-LUMO gap（eV）。GFN-FF 无轨道概念，输出中不存在 → None。"""
    m = _RE_GAP.search(stdout)
    return float(m.group(1)) if m else None


def parse_dipole_debye(stdout: str) -> float | None:
    """分子偶极矩模（Debye），取 'molecular dipole' 段 full: 行末列。"""
    idx = stdout.find("molecular dipole")
    if idx < 0:
        return None
    m = _RE_DIPOLE_FULL.search(stdout, idx)
    return float(m.group(1)) if m else None


# ---------------------------------------------------------------- 3D 构象生成

def _embed_mol(mol: Chem.Mol, n_confs: int = 1, seed: int = 42) -> Chem.Mol | None:
    """ETKDGv3 嵌入（返回加氢后的分子；失败返回 None）。"""
    mol_h = Chem.AddHs(mol)
    params = AllChem.ETKDGv3()
    params.randomSeed = seed
    params.numThreads = 0
    if n_confs > 1:
        params.pruneRmsThresh = 0.5
    try:
        cids = AllChem.EmbedMultipleConfs(mol_h, numConfs=n_confs, params=params)
    except Exception:
        cids = []
    if not cids:
        params.randomSeed = -1
        try:
            cids = AllChem.EmbedMultipleConfs(mol_h, numConfs=n_confs, params=params)
        except Exception:
            cids = []
    if not cids:
        return None
    return mol_h


def _forcefield_energy(mol: Chem.Mol, conf_id: int, optimize: bool = True
                       ) -> float | None:
    """UFF 预优化并返回力场能量；UFF 不支持时回退 MMFF。均不可用返回 None。"""
    try:
        if optimize:
            AllChem.UFFOptimizeMolecule(mol, confId=conf_id, maxIters=500)
        ff = AllChem.UFFGetMoleculeForceField(mol, confId=conf_id)
        return float(ff.CalcEnergy())
    except Exception:
        pass
    try:
        if optimize:
            AllChem.MMFFOptimizeMolecule(mol, confId=conf_id, maxIters=500)
        props = AllChem.MMFFGetMoleculeProperties(mol)
        ff = AllChem.MMFFGetMoleculeForceField(mol, props, confId=conf_id)
        return float(ff.CalcEnergy())
    except Exception:
        return None


def embed_monomer_xyz(smiles: str) -> str:
    """单体 → ETKDG 3D + UFF 预优化 → xyz 文本。"""
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise DftError(f"SMILES 无法解析：{smiles[:80]}")
    mol_h = _embed_mol(mol)
    if mol_h is None:
        raise DftError("3D 构象生成失败：请检查单体结构是否合理（如价位/环张力）")
    _forcefield_energy(mol_h, 0)
    return Chem.MolToXYZBlock(mol_h, confId=0)


def _embed_one(smiles: str, seed: int = 42) -> Chem.Mol | None:
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None
    mol_h = _embed_mol(mol, seed=seed)
    if mol_h is None:
        return None
    _forcefield_energy(mol_h, 0)
    return mol_h


def _orientations(mol_a: Chem.Mol, mol_b: Chem.Mol, n: int, seed: int):
    """生成 n 个「B 相对 A」的取向：随机旋转 B + 沿随机方向平移到不重叠距离。

    直接 ETKDG 嵌入 dot-disconnected 复合物会把一个 fragment 嵌进另一个的
    环心/内部（实测苯·甲醛曾把甲醛碳嵌入苯环中心），xtb 优化后塌成"共价
    融合"的虚假结构。改为：两单体各自独立 3D 化，再把 B 平移到
    r_A + r_B + 2.6 Å（保守不重叠），交由力场/xtb 把它们拉近成结合复合物。
    """
    import numpy as np

    rng = np.random.default_rng(seed)
    pos_a = mol_a.GetConformer(0).GetPositions()
    cen_a = pos_a.mean(axis=0)
    r_a = float(np.linalg.norm(pos_a - cen_a, axis=1).max())
    pos_b0 = mol_b.GetConformer(0).GetPositions()
    cen_b = pos_b0.mean(axis=0)
    r_b = float(np.linalg.norm(pos_b0 - cen_b, axis=1).max())
    sep = r_a + r_b + 2.6  # Å，保证任意取向下不穿插

    out = []
    for _ in range(max(1, n)):
        # 随机旋转矩阵（均匀采样：随机轴 + 随机角）
        axis = rng.normal(size=3)
        axis /= np.linalg.norm(axis) + 1e-12
        theta = rng.uniform(0, 2 * np.pi)
        k = axis
        kx = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
        rot = (np.eye(3) + np.sin(theta) * kx
               + (1 - np.cos(theta)) * (kx @ kx))
        direction = rng.normal(size=3)
        direction /= np.linalg.norm(direction) + 1e-12
        new_b = (pos_b0 - cen_b) @ rot.T + cen_a + direction * sep
        out.append(new_b)
    return out


def _combined_with_b(mol_a: Chem.Mol, mol_b: Chem.Mol, pos_b) -> Chem.Mol:
    """合并两加氢分子，B 的构象坐标替换为 pos_b。"""
    import numpy as np
    from rdkit.Geometry import Point3D

    combined = Chem.CombineMols(mol_a, mol_b)
    # CombineMols 不拷贝 RingInfo，后续 UFF/写 xyz 会触发 RingInfo 未初始化告警
    combined.UpdatePropertyCache(strict=False)
    Chem.FastFindRings(combined)
    conf = combined.GetConformer(0)
    n_a = mol_a.GetNumAtoms()
    for i in range(mol_b.GetNumAtoms()):
        x, y, z = (float(v) for v in np.asarray(pos_b[i]))
        conf.SetAtomPosition(n_a + i, Point3D(x, y, z))
    return combined


def embed_complex_xyz(smiles_a: str, smiles_b: str,
                      n_orientations: int = 4, seed: int = 42) -> str:
    """两单体非共价复合物初猜 → 力场能量最低取向的 xyz。

    流程：两单体各自 ETKDG+UFF → B 多取向摆放（不重叠）→ 组合体 UFF 优化
    → 取力场能量最低者。对应方案 §3.4「多个初猜取向取最低能」。
    """
    mol_a = _embed_one(smiles_a, seed=seed)
    mol_b = _embed_one(smiles_b, seed=seed + 1)
    if mol_a is None or mol_b is None:
        raise DftError("SMILES 无法解析或 3D 化失败，无法构造复合物初猜")

    best_xyz, best_e = None, None
    for pos_b in _orientations(mol_a, mol_b, n_orientations, seed):
        combined = _combined_with_b(mol_a, mol_b, pos_b)
        e = _forcefield_energy(combined, 0)  # UFF 预优化（含非键拉近）
        if best_xyz is None or (e is not None and (best_e is None or e < best_e)):
            best_xyz = Chem.MolToXYZBlock(combined, confId=0)
            best_e = e if e is not None else best_e
    if best_xyz is None:
        raise DftError("复合物 3D 构象生成失败：两单体组合可能难以嵌入同一空间")
    return best_xyz


# ---------------------------------------------------------------- xtb 子进程

def _check_elements(mol: Chem.Mol) -> None:
    for atom in mol.GetAtoms():
        z = atom.GetAtomicNum()
        if z > _XTB_MAX_Z:
            raise DftError(
                f"单体包含 xTB 不支持的元素 {atom.GetSymbol()}（Z={z}），"
                f"当前引擎仅覆盖 Z≤{_XTB_MAX_Z}")


def _classify_failure(stdout: str, stderr: str, returncode: int | None) -> str:
    """把 xtb 失败输出归类成中文原因。"""
    blob = f"{stdout}\n{stderr}".lower()
    if "element" in blob and ("no parameters" in blob or "not parametrized" in blob):
        return "包含 xTB 未参数化的元素，无法计算"
    if "normal termination" not in blob:
        if returncode is not None and returncode < 0:
            return f"计算进程异常终止（信号 {-returncode}）"
        return "几何优化未收敛或计算中途失败（可尝试改用「快速」档位重试）"
    return f"xtb 退出码 {returncode}，未得到有效能量"


def _run_xtb(xyz_block: str, args: list[str], cwd: Path, timeout: int,
             ) -> tuple[str, str | None]:
    """在 cwd 下跑一次 xtb --opt。

    Returns:
        (stdout 全文, xtbopt.xyz 内容或 None)

    Raises:
        DftError: 未安装引擎 / 超时 / 非 normal termination
    """
    exe = xtb_binary()
    if exe is None:
        raise DftError("未安装计算引擎：未找到 xtb 二进制（vendor/xtb/bin/xtb.exe），"
                       "无法执行量子化学计算")

    cwd.mkdir(parents=True, exist_ok=True)
    input_path = cwd / "input.xyz"
    input_path.write_text(xyz_block, encoding="utf-8")

    env = os.environ.copy()
    env["XTBPATH"] = str(xtb_share_dir())
    env.setdefault("OMP_NUM_THREADS", "4")
    env.setdefault("OMP_STACKSIZE", "1G")

    cmd = [str(exe), "input.xyz", "--opt", *args]
    creationflags = 0
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(
            cmd, cwd=str(cwd), env=env, capture_output=True, text=True,
            timeout=timeout, creationflags=creationflags, errors="replace")
    except subprocess.TimeoutExpired:
        raise DftError(
            f"计算超时（超过 {timeout} 秒仍未完成）：体系可能过大或优化不收敛，"
            "可改用「快速」档位或简化单体")
    except FileNotFoundError:
        raise DftError("未安装计算引擎：xtb 二进制无法执行，请检查 vendor/xtb 是否完整")
    except OSError as exc:
        raise DftError(f"计算引擎启动失败：{exc}")

    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    # 注意：xtb 的 "normal termination of xtb" 打在 stderr，能量等在 stdout
    if "normal termination of xtb" not in (stdout + stderr):
        raise DftError(_classify_failure(stdout, stderr, proc.returncode))

    opt_xyz = None
    opt_path = cwd / "xtbopt.xyz"
    if opt_path.is_file():
        try:
            opt_xyz = opt_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            opt_xyz = None
    return stdout, opt_xyz


# ---------------------------------------------------------------- 主管线

def compute_binding(smiles_a: str, smiles_b: str, method: str = "gfn2",
                    on_stage=None, jobs_root: Path | None = None) -> dict:
    """计算两单体结合能与量化描述符。

    Args:
        smiles_a/smiles_b: 原始单体 SMILES（内部做 RDKit 规范化）
        method: "gfnff" | "gfn2"
        on_stage: 可选回调 on_stage(hint: str)，用于任务进度提示
        jobs_root: 任务临时目录根（默认 user_data_root()/dft_jobs）

    Returns:
        结果 dict（能量 / gap / 偶极矩 / 复合物优化后 xyz / 耗时）

    Raises:
        DftError: 任何一步失败（message 为中文原因）
    """
    if method not in METHODS:
        raise DftError(f"未知方法档位：{method}（可选 gfnff / gfn2）")

    canon_a = canonicalize_smiles(smiles_a)
    canon_b = canonicalize_smiles(smiles_b)
    if canon_a is None:
        raise DftError(f"单体 A 的 SMILES 无法解析：{smiles_a[:80]}")
    if canon_b is None:
        raise DftError(f"单体 B 的 SMILES 无法解析：{smiles_b[:80]}")
    for canon in (canon_a, canon_b):
        mol = Chem.MolFromSmiles(canon)
        if mol is not None:
            _check_elements(mol)

    if xtb_binary() is None:
        raise DftError("未安装计算引擎：未找到 xtb 二进制（vendor/xtb/bin/xtb.exe），"
                       "无法执行量子化学计算")

    if jobs_root is None:
        jobs_root = runtime_config.user_data_root() / "dft_jobs"
    tag = hashlib.sha1(
        f"{canon_a}|{canon_b}|{method}|{time.time_ns()}".encode()).hexdigest()[:12]
    job_dir = Path(tempfile.mkdtemp(prefix=f"dft_{tag}_", dir=_ensure_dir(jobs_root)))

    timeout = method_timeout(method)
    args = METHODS[method]["args"]
    started = time.time()

    def stage(hint: str) -> None:
        if on_stage is not None:
            try:
                on_stage(hint)
            except Exception:
                pass

    try:
        stage("正在生成单体 A 的 3D 构象…")
        xyz_a = embed_monomer_xyz(canon_a)
        stage("正在优化单体 A 几何…")
        out_a, _ = _run_xtb(xyz_a, args, job_dir / "monomer_a", timeout)

        stage("正在生成单体 B 的 3D 构象…")
        xyz_b = embed_monomer_xyz(canon_b)
        stage("正在优化单体 B 几何…")
        out_b, _ = _run_xtb(xyz_b, args, job_dir / "monomer_b", timeout)

        stage("正在构造复合物初猜…")
        xyz_c = embed_complex_xyz(canon_a, canon_b)
        stage("正在优化复合物几何…")
        out_c, opt_xyz = _run_xtb(xyz_c, args, job_dir / "complex", timeout)

        stage("正在解析计算结果…")
        e_a, e_b, e_c = (parse_energy(out_a), parse_energy(out_b),
                         parse_energy(out_c))
        if e_a is None or e_b is None or e_c is None:
            raise DftError("xtb 输出中未找到总能量（TOTAL ENERGY），无法计算结合能")
        e_bind = e_c - e_a - e_b

        return {
            "smiles_a": canon_a,
            "smiles_b": canon_b,
            "method": method,
            "method_label": METHODS[method]["label"],
            "e_bind_hartree": e_bind,
            "e_bind_kcal": e_bind * HARTREE_TO_KCAL,
            "e_bind_kj": e_bind * HARTREE_TO_KJ,
            "energies_hartree": {"a": e_a, "b": e_b, "complex": e_c},
            "gap_ev": {"a": parse_gap_ev(out_a),
                       "b": parse_gap_ev(out_b),
                       "complex": parse_gap_ev(out_c)},
            "dipole_debye": {"a": parse_dipole_debye(out_a),
                             "b": parse_dipole_debye(out_b),
                             "complex": parse_dipole_debye(out_c)},
            "complex_xyz": opt_xyz or xyz_c,
            "elapsed_sec": round(time.time() - started, 2),
        }
    finally:
        # xtb 每个子目录会产生大量中间文件，尽力清理（失败不影响结果）
        import shutil
        shutil.rmtree(job_dir, ignore_errors=True)


def _ensure_dir(p: Path) -> str:
    p.mkdir(parents=True, exist_ok=True)
    return str(p)
