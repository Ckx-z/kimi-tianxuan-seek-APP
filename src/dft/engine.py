"""xTB 半经验计算引擎：缩合二聚体与第三物质 X 的结合能 + gap/偶极矩解析。

DFT 2.0 管线（docs/DFT2.0设计方案.md §一）：
    醛单体 + 胺单体 → [亚胺缩合] 二聚体 D（dimer.make_dimer）
    D 与 X 各自 RDKit ETKDG 3D + UFF 预优化 → xyz
           → xtb --opt（GFN-FF 或 GFN2-xTB）
           → E_bind = E(D·X 复合物) − E(D) − E(X)（hartree → kcal/mol、kJ/mol）

X（第三物质）四种类型（x_type）：
  - self_stack（默认）：X = D 自身（二聚体·二聚体堆积，π-π/自聚集倾向）
  - solvent：          X = 内置溶剂分子（SOLVENTS 表，solvent_id 指定）
  - other_dimer：      X = 另一组醛/胺单体缩合形成的二聚体
  - custom：           X = 自定义 SMILES 分子

复合物初猜：两分子各自独立 3D 化后多取向不重叠摆放（见 _orientations），
UFF 预优化取力场能量最低取向。

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

try:
    from src.dft import dimer as dimer_mod
    DimerError = dimer_mod.DimerError
except ImportError:  # pragma: no cover
    from dft import dimer as dimer_mod  # type: ignore
    DimerError = dimer_mod.DimerError

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

# 复合物原子数超过该阈值时给出「体系较大」进度提示
LARGE_SYSTEM_ATOMS = 150

# 第三物质 X 的可选类型
X_TYPES = ("self_stack", "solvent", "other_dimer", "custom")

# 计算模式：dimer（默认，醛胺缩合二聚体·X）| pair（任意双分子 A···B 直接结合）
MODES = ("dimer", "pair")

# pair 模式下 X 描述固定文案（dimer_smiles 置 null，无第三物质概念）
PAIR_X_DESCRIPTION = "A···B 直接结合"

# 内置常用溶剂表（DFT 2.0 §一：溶剂分子作为 X）
SOLVENTS: list[dict] = [
    {"id": "toluene", "name_zh": "甲苯", "smiles": "Cc1ccccc1"},
    {"id": "mesitylene", "name_zh": "均三甲苯", "smiles": "Cc1cc(C)cc(C)c1"},
    {"id": "dioxane", "name_zh": "1,4-二氧六环", "smiles": "C1COCCO1"},
    {"id": "dmf", "name_zh": "DMF（N,N-二甲基甲酰胺）", "smiles": "CN(C)C=O"},
    {"id": "water", "name_zh": "水", "smiles": "O"},
    {"id": "chloroform", "name_zh": "氯仿", "smiles": "ClC(Cl)Cl"},
    {"id": "ethanol", "name_zh": "乙醇", "smiles": "CCO"},
    {"id": "heptane", "name_zh": "正庚烷", "smiles": "CCCCCCC"},
]


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


def solvent_by_id(solvent_id: str) -> dict | None:
    """按 id 查内置溶剂；未找到返回 None。"""
    for s in SOLVENTS:
        if s["id"] == solvent_id:
            return s
    return None


def resolve_x(x_type: str, dimer_smiles: str, *, solvent_id: str | None = None,
              ald2_smiles: str | None = None, amine2_smiles: str | None = None,
              custom_smiles: str | None = None
              ) -> tuple[str, str, str]:
    """解析第三物质 X。

    Returns:
        (x_smiles_canonical, x_description_中文, x_cache_part)
        x_cache_part 用于缓存 key（形如 self_stack / solvent:toluene /
        other_dimer:<smiles> / custom:<smiles>）。

    Raises:
        DftError: 未知类型 / 缺参数 / SMILES 非法（中文原因）
    """
    if x_type == "self_stack":
        return dimer_smiles, "自身堆积（二聚体·二聚体）", "self_stack"
    if x_type == "solvent":
        if not (solvent_id or "").strip():
            raise DftError("选择「溶剂分子」时必须指定 solvent_id（内置溶剂 id）")
        s = solvent_by_id(solvent_id.strip())
        if s is None:
            raise DftError(
                f"未知溶剂：{solvent_id}（可用 {', '.join(x['id'] for x in SOLVENTS)}）")
        canon = canonicalize_smiles(s["smiles"])
        return canon, f"溶剂分子：{s['name_zh']}", f"solvent:{s['id']}"
    if x_type == "other_dimer":
        if not (ald2_smiles or "").strip() or not (amine2_smiles or "").strip():
            raise DftError("选择「另一组单体形成的二聚体」时必须提供"
                           " ald2_smiles 与 amine2_smiles")
        try:
            other = dimer_mod.make_dimer(ald2_smiles, amine2_smiles)
        except DimerError as exc:
            raise DftError(f"另一组单体无法形成二聚体：{exc}")
        return other["smiles"], "另一组单体缩合形成的二聚体", \
            f"other_dimer:{other['smiles']}"
    if x_type == "custom":
        if not (custom_smiles or "").strip():
            raise DftError("选择「自定义分子」时必须提供 custom_smiles")
        canon = canonicalize_smiles(custom_smiles)
        if canon is None:
            raise DftError(
                f"自定义分子的 SMILES 无法解析：{(custom_smiles or '')[:80]}")
        return canon, "自定义分子", f"custom:{canon}"
    raise DftError(
        f"未知的 X 类型：{x_type}（可选 {' / '.join(X_TYPES)}）")


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
    """两分子非共价复合物初猜 → 力场能量最低取向的 xyz。

    流程：两分子各自 ETKDG+UFF → B 多取向摆放（不重叠）→ 组合体 UFF 优化
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

def _xyz_atom_count(xyz_block: str) -> int:
    """xyz 文本第一行的原子数；解析失败返回 0。"""
    try:
        return int(xyz_block.strip().splitlines()[0])
    except Exception:
        return 0


def _fragment_ranges(n_a: int, n_total: int) -> dict:
    """复合物 xyz 中两个片段的原子序区间（0 基、左闭右开）。

    复合物拼接顺序固定为先主体（二聚体 / 分子 A）后客体（X / 分子 B），
    xtb 优化不改变原子顺序，故该区间对优化后几何同样有效。
    """
    return {"a": [0, n_a], "b": [n_a, n_total]}


def compute_binding(ald_smiles: str, amine_smiles: str, method: str = "gfn2",
                    x_type: str = "self_stack",
                    solvent_id: str | None = None,
                    ald2_smiles: str | None = None,
                    amine2_smiles: str | None = None,
                    custom_smiles: str | None = None,
                    on_stage=None, jobs_root: Path | None = None) -> dict:
    """计算「缩合二聚体 D 与第三物质 X」的结合能与量化描述符。

    流程：醛/胺单体 → 亚胺缩合二聚体 D（dimer.make_dimer）→ 解析 X
    → D / X / D·X 复合物各自 xtb --opt → E_bind = E(D·X) − E(D) − E(X)。

    Args:
        ald_smiles/amine_smiles: 醛/胺单体 SMILES（内部做 RDKit 规范化）
        method: "gfnff" | "gfn2"
        x_type: "self_stack"（默认）| "solvent" | "other_dimer" | "custom"
        solvent_id / ald2_smiles / amine2_smiles / custom_smiles:
            各 x_type 对应的补充参数
        on_stage: 可选回调 on_stage(hint: str)，用于任务进度提示
        jobs_root: 任务临时目录根（默认 user_data_root()/dft_jobs）

    Returns:
        结果 dict（二聚体 SMILES / X 描述 / 能量 / gap / 偶极矩 /
        复合物优化后 xyz / 耗时）；smiles_a/smiles_b 保留为规范化醛/胺单体
        （供收藏联动等下游兼容使用）。

    Raises:
        DftError: 任何一步失败（message 为中文原因）
    """
    if method not in METHODS:
        raise DftError(f"未知方法档位：{method}（可选 gfnff / gfn2）")

    canon_ald = canonicalize_smiles(ald_smiles)
    canon_amine = canonicalize_smiles(amine_smiles)
    if canon_ald is None:
        raise DftError(f"醛单体的 SMILES 无法解析：{(ald_smiles or '')[:80]}")
    if canon_amine is None:
        raise DftError(f"胺单体的 SMILES 无法解析：{(amine_smiles or '')[:80]}")
    for canon in (canon_ald, canon_amine):
        mol = Chem.MolFromSmiles(canon)
        if mol is not None:
            _check_elements(mol)

    if xtb_binary() is None:
        raise DftError("未安装计算引擎：未找到 xtb 二进制（vendor/xtb/bin/xtb.exe），"
                       "无法执行量子化学计算")

    def stage(hint: str) -> None:
        if on_stage is not None:
            try:
                on_stage(hint)
            except Exception:
                pass

    # 1. 缩合二聚体
    stage("正在生成缩合二聚体（醛 + 胺 → 亚胺）…")
    try:
        dim = dimer_mod.make_dimer(canon_ald, canon_amine)
    except DimerError as exc:
        raise DftError(f"二聚体生成失败：{exc}")
    dimer_smiles = dim["smiles"]

    # 2. 解析第三物质 X
    x_smiles, x_description, x_cache_part = resolve_x(
        x_type, dimer_smiles, solvent_id=solvent_id,
        ald2_smiles=ald2_smiles, amine2_smiles=amine2_smiles,
        custom_smiles=custom_smiles)
    mol_x = Chem.MolFromSmiles(x_smiles)
    if mol_x is not None:
        _check_elements(mol_x)

    if jobs_root is None:
        jobs_root = runtime_config.user_data_root() / "dft_jobs"
    tag = hashlib.sha1(
        f"{dimer_smiles}|{x_cache_part}|{method}|{time.time_ns()}".encode()
    ).hexdigest()[:12]
    job_dir = Path(tempfile.mkdtemp(prefix=f"dft_{tag}_", dir=_ensure_dir(jobs_root)))

    timeout = method_timeout(method)
    args = METHODS[method]["args"]
    started = time.time()

    try:
        stage("正在生成二聚体的 3D 构象…")
        xyz_d = embed_monomer_xyz(dimer_smiles)
        stage("正在优化二聚体几何…")
        out_d, _ = _run_xtb(xyz_d, args, job_dir / "dimer", timeout)

        if x_smiles == dimer_smiles:
            # 自身堆积：X 即 D，能量与描述符直接复用（省一次 xtb）
            stage("X 为二聚体自身（自身堆积），复用二聚体计算结果…")
            out_x = out_d
        else:
            stage("正在生成 X 的 3D 构象…")
            xyz_x = embed_monomer_xyz(x_smiles)
            stage("正在优化 X 几何…")
            out_x, _ = _run_xtb(xyz_x, args, job_dir / "x", timeout)

        stage("正在构造 D·X 复合物初猜…")
        xyz_c = embed_complex_xyz(dimer_smiles, x_smiles)
        n_atoms = _xyz_atom_count(xyz_c)
        if n_atoms > LARGE_SYSTEM_ATOMS:
            stage(f"体系较大（复合物 {n_atoms} 个原子），预计耗时较长，请耐心等待…")
        else:
            stage("正在优化 D·X 复合物几何…")
        out_c, opt_xyz = _run_xtb(xyz_c, args, job_dir / "complex", timeout)

        stage("正在解析计算结果…")
        e_d, e_x, e_c = (parse_energy(out_d), parse_energy(out_x),
                         parse_energy(out_c))
        if e_d is None or e_x is None or e_c is None:
            raise DftError("xtb 输出中未找到总能量（TOTAL ENERGY），无法计算结合能")
        e_bind = e_c - e_d - e_x

        return {
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
            # 原始 X 参数回显：前端从历史结果借缓存任务导出输入文件时用
            "x_request": {
                "solvent_id": solvent_id if x_type == "solvent" else None,
                "ald2_smiles": ald2_smiles if x_type == "other_dimer" else None,
                "amine2_smiles": amine2_smiles if x_type == "other_dimer" else None,
                "custom_smiles": custom_smiles if x_type == "custom" else None,
            },
            "method": method,
            "method_label": METHODS[method]["label"],
            "e_bind_hartree": e_bind,
            "e_bind_kcal": e_bind * HARTREE_TO_KCAL,
            "e_bind_kj": e_bind * HARTREE_TO_KJ,
            "energies_hartree": {"dimer": e_d, "x": e_x, "complex": e_c},
            "gap_ev": {"dimer": parse_gap_ev(out_d),
                       "x": parse_gap_ev(out_x),
                       "complex": parse_gap_ev(out_c)},
            "dipole_debye": {"dimer": parse_dipole_debye(out_d),
                             "x": parse_dipole_debye(out_x),
                             "complex": parse_dipole_debye(out_c)},
            "complex_atom_count": n_atoms,
            "complex_xyz": opt_xyz or xyz_c,
            # 复合物 xyz 片段区间：a=二聚体 [0,n_d)，b=X [n_d,total)
            "fragment_ranges": _fragment_ranges(_xyz_atom_count(xyz_d), n_atoms),
            "elapsed_sec": round(time.time() - started, 2),
        }
    finally:
        # xtb 每个子目录会产生大量中间文件，尽力清理（失败不影响结果）
        import shutil
        shutil.rmtree(job_dir, ignore_errors=True)


def compute_pair_binding(smiles_a: str, smiles_b: str, method: str = "gfn2",
                         on_stage=None, jobs_root: Path | None = None) -> dict:
    """任意双分子模式（选项2）：A···B 复合物结合能，不经过二聚体生成。

    E_bind = E(A·B 复合物) − E(A) − E(B)。ald/amine 字段位复用为分子 A/B，
    结果字段与 compute_binding 对齐（energies/gap/dipole 的 "dimer"/"x" 键
    在 pair 模式下分别对应分子 A / 分子 B），dimer_smiles 置 None，
    x_description 固定为「A···B 直接结合」，x_type 置 None。

    Raises:
        DftError: 任何一步失败（message 为中文原因）
    """
    if method not in METHODS:
        raise DftError(f"未知方法档位：{method}（可选 gfnff / gfn2）")

    canon_a = canonicalize_smiles(smiles_a)
    canon_b = canonicalize_smiles(smiles_b)
    if canon_a is None:
        raise DftError(f"分子 A 的 SMILES 无法解析：{(smiles_a or '')[:80]}")
    if canon_b is None:
        raise DftError(f"分子 B 的 SMILES 无法解析：{(smiles_b or '')[:80]}")
    for canon in (canon_a, canon_b):
        mol = Chem.MolFromSmiles(canon)
        if mol is not None:
            _check_elements(mol)

    if xtb_binary() is None:
        raise DftError("未安装计算引擎：未找到 xtb 二进制（vendor/xtb/bin/xtb.exe），"
                       "无法执行量子化学计算")

    def stage(hint: str) -> None:
        if on_stage is not None:
            try:
                on_stage(hint)
            except Exception:
                pass

    if jobs_root is None:
        jobs_root = runtime_config.user_data_root() / "dft_jobs"
    tag = hashlib.sha1(
        f"pair|{canon_a}|{canon_b}|{method}|{time.time_ns()}".encode()
    ).hexdigest()[:12]
    job_dir = Path(tempfile.mkdtemp(prefix=f"dft_{tag}_", dir=_ensure_dir(jobs_root)))

    timeout = method_timeout(method)
    args = METHODS[method]["args"]
    started = time.time()

    try:
        stage("正在生成分子 A 的 3D 构象…")
        xyz_a = embed_monomer_xyz(canon_a)
        stage("正在优化分子 A 几何…")
        out_a, _ = _run_xtb(xyz_a, args, job_dir / "a", timeout)

        if canon_b == canon_a:
            # A 与 B 同分子：能量与描述符直接复用（省一次 xtb）
            stage("分子 B 与 A 相同，复用 A 的计算结果…")
            out_b = out_a
        else:
            stage("正在生成分子 B 的 3D 构象…")
            xyz_b = embed_monomer_xyz(canon_b)
            stage("正在优化分子 B 几何…")
            out_b, _ = _run_xtb(xyz_b, args, job_dir / "b", timeout)

        stage("正在构造 A···B 复合物初猜…")
        xyz_c = embed_complex_xyz(canon_a, canon_b)
        n_atoms = _xyz_atom_count(xyz_c)
        if n_atoms > LARGE_SYSTEM_ATOMS:
            stage(f"体系较大（复合物 {n_atoms} 个原子），预计耗时较长，请耐心等待…")
        else:
            stage("正在优化 A···B 复合物几何…")
        out_c, opt_xyz = _run_xtb(xyz_c, args, job_dir / "complex", timeout)

        stage("正在解析计算结果…")
        e_a, e_b, e_c = (parse_energy(out_a), parse_energy(out_b),
                         parse_energy(out_c))
        if e_a is None or e_b is None or e_c is None:
            raise DftError("xtb 输出中未找到总能量（TOTAL ENERGY），无法计算结合能")
        e_bind = e_c - e_a - e_b

        return {
            "mode": "pair",
            "smiles_a": canon_a,
            "smiles_b": canon_b,
            "dimer_smiles": None,
            "dimer_multi_site": False,
            "dimer_note": None,
            "x_type": None,
            "x_smiles": canon_b,
            "x_description": PAIR_X_DESCRIPTION,
            "x_cache_part": f"pair:{canon_b}",
            "x_request": {"solvent_id": None, "ald2_smiles": None,
                          "amine2_smiles": None, "custom_smiles": None},
            "method": method,
            "method_label": METHODS[method]["label"],
            "e_bind_hartree": e_bind,
            "e_bind_kcal": e_bind * HARTREE_TO_KCAL,
            "e_bind_kj": e_bind * HARTREE_TO_KJ,
            # "dimer"/"x" 键与二聚体模式对齐，pair 模式下分别对应分子 A / B
            "energies_hartree": {"dimer": e_a, "x": e_b, "complex": e_c},
            "gap_ev": {"dimer": parse_gap_ev(out_a),
                       "x": parse_gap_ev(out_b),
                       "complex": parse_gap_ev(out_c)},
            "dipole_debye": {"dimer": parse_dipole_debye(out_a),
                             "x": parse_dipole_debye(out_b),
                             "complex": parse_dipole_debye(out_c)},
            "complex_atom_count": n_atoms,
            "complex_xyz": opt_xyz or xyz_c,
            # 复合物 xyz 片段区间：a=分子 A [0,n_a)，b=分子 B [n_a,total)
            "fragment_ranges": _fragment_ranges(_xyz_atom_count(xyz_a), n_atoms),
            "elapsed_sec": round(time.time() - started, 2),
        }
    finally:
        import shutil
        shutil.rmtree(job_dir, ignore_errors=True)


def _ensure_dir(p: Path) -> str:
    p.mkdir(parents=True, exist_ok=True)
    return str(p)
