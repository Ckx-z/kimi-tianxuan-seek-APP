"""构象采样引擎（v1.5.0）：自动检索低能构象 + 手动摆放几何处理。

- generate_conformers_etkdg：RDKit ETKDG 多构象生成 + MMFF94 优化 + 能量窗口
  过滤 + RMSD 去重（零额外依赖，首版必可用）。
- generate_conformers_crest：CREST（iMTD-GC，GFN2）全自动构象搜索（推荐引擎，
  需 conda 安装 crest，见 scripts/install_psi4_env.bat 扩展或 crest_binary()）。
- conformer_engines：引擎可用性探测（并入 GET /api/dft/backends 展示）。
- 产物统一为 {id, xyz, rel_e_kj, rel_e_kcal, boltzmann_w}，可直接经
  complex_xyz 注入 xTB / Psi4 计算（engine/psi4_backend 均支持）。
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from . import engine

try:
    from src import runtime_config
except ImportError:  # pragma: no cover
    import runtime_config  # type: ignore

KT_KCAL = 0.5922  # 室温约 298K 的 kT（kcal/mol）
DEFAULT_MAX_CONFS = 20
DEFAULT_E_WINDOW_KJ = 10.0


# ---------------------------------------------------------------- RDKit ETKDG


def generate_conformers_etkdg(smiles: str, n_gen: int = 50,
                              max_confs: int = DEFAULT_MAX_CONFS,
                              e_window_kj: float = DEFAULT_E_WINDOW_KJ,
                              seed: int = 42) -> list[dict]:
    """ETKDG 多构象生成 + MMFF94 优化 + 能量窗口 + RMSD 去重。

    Args:
        smiles: 分子 SMILES（可含 H；构象生成前加 H）
        n_gen: ETKDG 生成尝试数
        max_confs: 保留上限（能量窗口内仍超限时按能量截断）
        e_window_kj: 相对全局最低能构象的能量窗口（ΔE ≤ 该值才保留）
        seed: 随机种子（可复现）

    Returns:
        [{id, xyz, rel_e_kj, rel_e_kcal, boltzmann_w}] 按能量升序；
        失败返回 []（调用方据此降级提示）。
    """
    from rdkit import Chem
    from rdkit.Chem import AllChem

    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return []
    mol = Chem.AddHs(mol)
    try:
        # 兼容新旧 RDKit API：经典 kwargs 形式（ETKDG v2 默认），
        # params=ETKDGv3() 关键字形式在部分 RDKit 版本不受支持
        ids = AllChem.EmbedMultipleConfs(mol, numConfs=n_gen, randomSeed=seed,
                                         maxAttempts=5000)
    except Exception:
        return []
    if not ids:
        return []

    def _to_xyz_block(cid: int) -> str:
        conf = mol.GetConformer(cid)
        lines = [str(mol.GetNumAtoms()), "conformer"]
        for i, a in enumerate(mol.GetAtoms()):
            p = conf.GetAtomPosition(i)
            lines.append(f"{a.GetSymbol()} {p.x:.6f} {p.y:.6f} {p.z:.6f}")
        return "\n".join(lines)

    # 能量排序：优先 xTB(gfnff) 单点（物理意义可靠，MMFF 在多构象上可能退化）；
    # xTB 不可用时回退 MMFF 优化能（kcal/mol → kJ/mol 统一）。
    use_xtb = engine.xtb_binary() is not None
    energies: list[tuple[int, float]] = []
    for cid in ids:
        try:
            ff = AllChem.MMFFGetMoleculeForceField(
                mol, AllChem.MMFFGetMoleculeProperties(mol), confId=cid)
            if ff is None:
                continue
            ff.Minimize(maxIts=500)
            if not use_xtb:
                energies.append((cid, float(ff.CalcEnergy()) * 4.184))
                continue
            block = _to_xyz_block(cid)
            work = Path(tempfile.mkdtemp(prefix="dft_conf_sp_"))
            try:
                out, _ = engine._run_xtb(block, ["--gfnff"], work, 180,
                                         opt=False)
                e_h = engine.parse_energy(out)
                if e_h is None:
                    continue
                energies.append((cid, e_h * 2625.5))  # Hartree → kJ/mol
            finally:
                import shutil as _shutil
                _shutil.rmtree(work, ignore_errors=True)
        except Exception:
            continue
    if not energies:
        return []
    energies.sort(key=lambda t: t[1])
    e_min = energies[0][1]

    kept: list[dict] = []
    kept_xyzs: list[str] = []
    for cid, e in energies:
        rel_kj = e - e_min  # energies 统一为 kJ/mol
        if rel_kj > e_window_kj:
            continue
        if len(kept) >= max_confs:
            break
        block = _to_xyz_block(cid)
        # RMSD 去重（粗判据：与已保留构象最小 RMSD < 0.4 Å 视为重复）
        if _min_rmsd(block, kept_xyzs) < 0.4:
            continue
        kept.append({
            "id": f"etkdg-{len(kept):02d}",
            "xyz": block,
            "rel_e_kj": round(rel_kj, 3),
            "rel_e_kcal": round(rel_kj / 4.184, 3),
            "boltzmann_w": round(math.exp(-rel_kj / (KT_KCAL * 4.184)), 6),
        })
        kept_xyzs.append(block)
    return kept


def _xyz_coords(xyz_block: str) -> list[tuple[float, float, float]]:
    """解析 xyz 文本为坐标三元组列表（前两行表头跳过）。"""
    out: list[tuple[float, float, float]] = []
    for line in xyz_block.strip().splitlines()[2:]:
        parts = line.split()
        if len(parts) < 4:
            continue
        try:
            out.append((float(parts[1]), float(parts[2]), float(parts[3])))
        except ValueError:
            continue
    return out


def _min_rmsd(xyz_block: str, others: list[str]) -> float:
    """与已保留构象的最小 RMSD（近似：等原子数下坐标差平方均值开方）。"""
    if not others:
        return 1e9
    p1 = _xyz_coords(xyz_block)
    if not p1:
        return 1e9
    best = 1e9
    for other in others:
        p2 = _xyz_coords(other)
        if len(p1) != len(p2):
            continue
        s = sum((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2
                for a, b in zip(p1, p2))
        best = min(best, math.sqrt(s / len(p1)))
    return best


# ---------------------------------------------------------------- CREST

CREST_DOCKER_IMAGE = "cof-crest:latest"
_DOCKER_DESKTOP_EXE = r"C:\Program Files\Docker\Docker\Docker Desktop.exe"
_DOCKER_INFO_CACHE: dict = {"ts": 0.0, "ready": False, "bin": None,
                            "launch_ts": 0.0}
_IMAGE_INFO_CACHE: dict = {"ts": 0.0, "ready": False}


def crest_binary() -> Path | None:
    """CREST 可执行文件探测（v1.5.1 扩展）：
    COF_CREST_BIN > runtime.local.json crest_bin > 系统 PATH（shutil.which）
    > psi4-env（Scripts / Library/bin）> 本机所有 conda envs
    （E:\\ANACONDA\\envs\\<env>\\{Scripts,Library\\bin}\\crest.exe）。"""
    import os
    val = os.environ.get("COF_CREST_BIN", "").strip()
    if val and Path(val).exists():
        return Path(val)
    cfg = runtime_config.load_local_config().get("crest_bin")
    if cfg and Path(str(cfg)).exists():
        return Path(str(cfg))
    # 系统 PATH（conda activate 后或手动加入 PATH 的安装）
    from_path = shutil.which("crest")
    if from_path:
        return Path(from_path)
    # psi4-env
    py = runtime_config.psi4_python()
    if py is not None:
        env_root = py.parent.parent  # <env>/python.exe → <env>
        for cand in (env_root / "Scripts" / "crest.exe",
                     env_root / "Library" / "bin" / "crest.exe",
                     env_root / "crest.exe"):
            if cand.exists():
                return cand
    # 其他 conda envs（Anaconda/Miniconda 根目录下的 envs/*）
    for anchor in (py.parent.parent.parent if py is not None else None,
                   Path(r"E:\ANACONDA"), Path(r"C:\ProgramData\Anaconda3"),
                   Path(r"C:\ProgramData\Miniconda3")):
        if anchor is None:
            continue
        envs_dir = anchor / "envs"
        if not envs_dir.is_dir():
            continue
        for env_dir in sorted(envs_dir.iterdir()):
            if not env_dir.is_dir():
                continue
            for cand in (env_dir / "Scripts" / "crest.exe",
                         env_dir / "Library" / "bin" / "crest.exe",
                         env_dir / "bin" / "crest.exe"):
                if cand.exists():
                    return cand
    # conda base 环境本身
    for cand in (Path(r"E:\ANACONDA\Scripts\crest.exe"),
                 Path(r"E:\ANACONDA\Library\bin\crest.exe")):
        if cand.exists():
            return cand
    return None


def _docker_binary() -> str | None:
    """docker CLI 探测：PATH > Docker Desktop 默认安装路径。"""
    if _DOCKER_INFO_CACHE["bin"]:
        return _DOCKER_INFO_CACHE["bin"]
    cand = shutil.which("docker")
    if not cand:
        default = Path(
            r"C:\Program Files\Docker\Docker\resources\bin\docker.exe")
        cand = str(default) if default.exists() else None
    _DOCKER_INFO_CACHE["bin"] = cand
    return cand


def docker_engine_ready() -> bool:
    """docker daemon 可用（docker info 退出码 0）。

    结果缓存 60s。引擎未运行但 Docker Desktop 已安装时，尝试自动拉起
    Docker Desktop 并等待引擎就绪（≤45s，10 分钟内只拉起一次），
    解决「重启后 Docker 未自启 → 误报未安装 CREST」的问题（v1.5.1）。
    """
    now = time.monotonic()
    if now - _DOCKER_INFO_CACHE["ts"] < 60:
        return _DOCKER_INFO_CACHE["ready"]
    ready = False
    docker = _docker_binary()
    if docker:
        try:
            proc = subprocess.run(
                [docker, "info"], capture_output=True, timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                if sys.platform == "win32" else 0)
            ready = proc.returncode == 0
        except Exception:
            ready = False
        if not ready and Path(_DOCKER_DESKTOP_EXE).exists() \
                and os.environ.get("COF_CREST_DOCKER_AUTOSTART", "1") != "0" \
                and now - _DOCKER_INFO_CACHE.get("launch_ts", 0.0) > 600:
            # 引擎未运行：尝试启动 Docker Desktop 并等一小会儿
            _DOCKER_INFO_CACHE["launch_ts"] = now
            try:
                subprocess.Popen(
                    [str(Path(_DOCKER_DESKTOP_EXE))],
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                    if sys.platform == "win32" else 0)
            except Exception:
                pass
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline and not ready:
                time.sleep(5)
                try:
                    proc = subprocess.run(
                        [docker, "info"], capture_output=True, timeout=15,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                        if sys.platform == "win32" else 0)
                    ready = proc.returncode == 0
                except Exception:
                    ready = False
    _DOCKER_INFO_CACHE.update(ts=now, ready=ready)
    return ready


def docker_crest_image_ready() -> bool:
    """cof-crest:latest 镜像存在且引擎可用（docker image inspect 成功）。

    结果缓存 60s；引擎未运行时会先触发 docker_engine_ready() 的自动拉起。
    """
    now = time.monotonic()
    if now - _IMAGE_INFO_CACHE["ts"] < 60:
        return _IMAGE_INFO_CACHE["ready"]
    ready = False
    docker = _docker_binary()
    if docker and docker_engine_ready():
        try:
            proc = subprocess.run(
                [docker, "image", "inspect", CREST_DOCKER_IMAGE],
                capture_output=True, timeout=30,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                if sys.platform == "win32" else 0)
            ready = proc.returncode == 0
        except Exception:
            ready = False
    _IMAGE_INFO_CACHE.update(ts=now, ready=ready)
    return ready


def crest_mode() -> str | None:
    """CREST 可用方式：'native'（本机二进制）| 'docker'（容器+镜像齐备）
    | None（未安装/引擎未运行/镜像缺失——由 conformer_engines 给出细分提示）。"""
    if crest_binary() is not None:
        return "native"
    if docker_crest_image_ready():
        return "docker"
    return None


def _auto_crest_threads(n_atoms: int) -> int:
    """按分子原子数自适应 CREST 线程数（v1.5.1）。

    CREST 的 iMTD-GC 是大量串行短 xtb 调用：小分子上高线程被 OpenMP
    线程池启动/同步开销淹没（实测 hexane：4T=37s vs 24T=350s），而大
    分子每个 SCF 重、高线程收益显著。以 crest_threads()（默认 24）为上限。
    """
    cap = runtime_config.crest_threads()
    if n_atoms <= 15:
        return min(4, cap)
    if n_atoms <= 30:
        return min(8, cap)
    if n_atoms <= 60:
        return min(12, cap)
    return cap


def generate_conformers_crest(xyz_block: str, max_confs: int = DEFAULT_MAX_CONFS,
                              e_window_kj: float = DEFAULT_E_WINDOW_KJ,
                              timeout: int = 3600,
                              threads: int | None = None) -> list[dict]:
    """CREST 全自动构象搜索（iMTD-GC + GFN2，含 NCI 交叉项）。

    输入单分子 xyz → crest --gfn2 --nci → 解析 crest_conformers.xyz
    （多帧 xyz，帧间以能量注释行分隔，能量升序）。按能量窗口与数量上限过滤。
    本机无 CREST 二进制但 Docker 镜像齐备时，自动回落容器运行。

    threads：并行线程数（None=按分子原子数自适应 4–24，见
    _auto_crest_threads）；经 OMP_NUM_THREADS 传给 crest/xtb，
    并置 OPENBLAS_NUM_THREADS=1 隔离 OpenBLAS 并行冲突。

    Returns:
        与 ETKDG 同构的列表；失败/未安装返回 []。
    """
    try:
        n_atoms = int(xyz_block.strip().splitlines()[0])
    except Exception:
        n_atoms = 0
    n_threads = int(threads) if threads else _auto_crest_threads(n_atoms)
    crest = crest_binary()
    if crest is None:
        if docker_crest_image_ready():
            return _generate_conformers_crest_docker(
                xyz_block, max_confs, e_window_kj, timeout,
                threads=n_threads)
        return []
    work = Path(tempfile.mkdtemp(prefix="dft_crest_"))
    try:
        inp = work / "input.xyz"
        inp.write_text(xyz_block, encoding="utf-8")
        # 本机二进制：补齐其 conda 环境的 XTBPATH/PATH（不依赖 conda activate），
        # 并传入线程数（默认 24）
        env = os.environ.copy()
        env["OMP_NUM_THREADS"] = str(n_threads)
        env["OPENBLAS_NUM_THREADS"] = "1"  # 同容器：隔离 OpenBLAS 并行冲突
        env.setdefault("OMP_STACKSIZE", "1G")
        bin_dir = crest.parent
        env_root = bin_dir.parent.parent  # <env>/Scripts → <env>
        for share in (env_root / "share" / "xtb",
                      env_root / "Library" / "share" / "xtb"):
            if share.is_dir():
                env["XTBPATH"] = str(share)
                break
        env["PATH"] = os.pathsep.join(
            [str(bin_dir), str(env_root / "Library" / "bin"), env.get("PATH", "")])
        cmd = [str(crest), str(inp), "--gfn2", "--nci", "--chrg", "0"]
        try:
            proc = subprocess.run(
                cmd, cwd=str(work), capture_output=True, text=True,
                timeout=timeout, errors="replace", encoding="utf-8", env=env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                if sys.platform == "win32" else 0)
        except subprocess.TimeoutExpired:
            return []
        if proc.returncode != 0:
            return []
        return _parse_crest_conformers(
            (work / "crest_conformers.xyz"), max_confs, e_window_kj)
    except Exception:
        return []
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _generate_conformers_crest_docker(xyz_block: str, max_confs: int,
                                      e_window_kj: float,
                                      timeout: int,
                                      threads: int = 24) -> list[dict]:
    """CREST 经 Docker 容器运行（cof-crest 镜像，conda-forge crest）。

    输入/产物经 docker cp 在容器与宿主间搬运（不用绑定挂载）：容器以
    root 写入挂载目录时，DrvFS 的 ACL 映射偶尔把产物映射成宿主用户不可读
    （PermissionError），docker cp 恒以宿主当前用户写入，确定性可读。
    """
    docker = _docker_binary()
    if docker is None:
        return []
    work = Path(tempfile.mkdtemp(prefix="dft_crest_docker_"))
    container = f"cof-crest-{uuid.uuid4().hex[:12]}"
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) \
        if sys.platform == "win32" else 0

    def _run(cmd: list[str], timeout_s: int) -> subprocess.CompletedProcess:
        return subprocess.run(
            cmd, capture_output=True, text=True, errors="replace",
            encoding="utf-8", timeout=timeout_s, creationflags=flags)

    try:
        inp = work / "input.xyz"
        inp.write_text(xyz_block, encoding="utf-8")
        crest_args = ["crest", "input.xyz", "--gfn2", "--nci", "--chrg", "0"]
        try:
            create = _run([docker, "create", "--name", container, "-w", "/work",
                           "-e", f"OMP_NUM_THREADS={int(threads)}",
                           "-e", "OMP_STACKSIZE=1G",
                           # OpenBLAS 与 xtb 自带 OpenMP 并行冲突会触发
                           # "may hang" 警告，多线程下小体系调用被线程开销
                           # 拖到分钟级；隔离 OpenBLAS 线程，仅保留 xtb OpenMP
                           "-e", "OPENBLAS_NUM_THREADS=1",
                           CREST_DOCKER_IMAGE, *crest_args], 60)
            if create.returncode != 0:
                return []
            cp_in = _run([docker, "cp", str(inp),
                          f"{container}:/work/input.xyz"], 60)
            if cp_in.returncode != 0:
                return []
            proc = _run([docker, "start", "-a", container], timeout)
        except subprocess.TimeoutExpired:
            return []
        ok = proc.returncode == 0
        if ok:
            out = work / "crest_conformers.xyz"
            cp_out = _run([docker, "cp",
                           f"{container}:/work/crest_conformers.xyz",
                           str(out)], 120)
            ok = cp_out.returncode == 0
        if not ok:
            return []
        return _parse_crest_conformers(
            work / "crest_conformers.xyz", max_confs, e_window_kj)
    except Exception:
        return []
    finally:
        try:
            _run([docker, "rm", "-f", container], 60)
        except Exception:
            pass
        shutil.rmtree(work, ignore_errors=True)


def _parse_crest_conformers(path: Path, max_confs: int,
                            e_window_kj: float) -> list[dict]:
    """解析 crest_conformers.xyz：能量升序多帧 xyz。

    注释行（帧头后第 2 行）两种格式均支持：
    - CREST 2.x：含 "energy: -12.3456" / "Erel = ..." 字样
    - CREST 3.x：裸能量数值（Hartree）
    """
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    frames: list[tuple[float, str]] = []
    current_lines: list[str] = []
    current_e: float | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # 帧头：原子数
        if len(current_lines) == 0:
            current_lines.append(stripped)
            continue
        # 注释行（帧头后第 2 行）：解析能量
        if len(current_lines) == 1:
            current_lines.append(stripped)
            low = stripped.lower()
            if "energy" in low or "erel" in low:
                m = re.search(r"(?:energy|erel)\s*[=:]?\s*([-+]?\d*\.?\d+)", low)
                current_e = float(m.group(1)) if m else None
            else:
                # CREST 3.x：裸能量数值
                try:
                    current_e = float(stripped.split()[0])
                except Exception:
                    current_e = None
            continue
        current_lines.append(stripped)
        try:
            n_atoms = int(current_lines[0])
        except ValueError:
            current_lines = []
            continue
        if len(current_lines) >= n_atoms + 2:
            block = "\n".join(current_lines[: n_atoms + 2])
            if current_e is not None:
                frames.append((current_e, block))
            current_lines = []
            current_e = None
    if not frames:
        return []
    frames.sort(key=lambda t: t[0])
    e_min = frames[0][0]
    kept: list[dict] = []
    for e, block in frames:
        rel_kj = (e - e_min) * 2625.5  # Hartree → kJ/mol
        if rel_kj > e_window_kj:
            continue
        if len(kept) >= max_confs:
            break
        kept.append({
            "id": f"crest-{len(kept):02d}",
            "xyz": block,
            "rel_e_kj": round(rel_kj, 3),
            "rel_e_kcal": round(rel_kj / 4.184, 3),
            "boltzmann_w": round(math.exp(-rel_kj / (KT_KCAL * 4.184)), 6),
        })
    return kept


# ---------------------------------------------------------------- 入口与缓存


def conformer_engines() -> dict:
    """引擎可用性（供 GET /api/dft/backends 展示与前端选择器）。

    v1.5.1：区分「引擎未运行 / 镜像缺失 / 真未安装」，给出对应修复提示，
    避免已装 Docker+镜像的用户因 Docker Desktop 未启动而看到笼统的未安装。
    """
    mode = crest_mode()  # 'native' | 'docker' | None
    native = crest_binary()
    docker = _docker_binary() if mode == "docker" else None
    docker_cli = _docker_binary()
    engine_up = docker_engine_ready() if docker_cli else False
    image_ok = docker_crest_image_ready()
    if mode is None:
        # 细分原因，给出可执行的提示
        if docker_cli and engine_up and not image_ok:
            hint = ("已检测到 Docker 引擎，但缺少 cof-crest 镜像：请运行 "
                    "scripts/setup_crest_docker.ps1（或 docker build，见 "
                    "scripts/docker/crest/Dockerfile）构建后自动生效")
        elif docker_cli and not engine_up:
            hint = ("已安装 Docker Desktop 但引擎未运行：请启动 Docker Desktop "
                    "（本应用已尝试自动拉起，若仍失败请手动打开并接受首次协议）")
        else:
            hint = ("conda install -c conda-forge crest（建议装入 psi4-env）"
                    "；或本机 Docker：运行 scripts/setup_crest_docker.ps1 构建"
                    " cof-crest 镜像后自动生效")
    else:
        hint = None
    return {
        "etkdg": {"installed": True,
                  "label": "RDKit ETKDG（零安装，轻量）"},
        "crest": {
            "installed": mode is not None,
            "mode": mode,
            "path": str(native) if native else (docker or None),
            "label": ("CREST（推荐，全自动构象搜索）" if mode == "native"
                      else "CREST（Docker 容器运行，cof-crest 镜像）"
                      if mode == "docker"
                      else "CREST（推荐，全自动构象搜索，需安装）"),
            "install_hint": hint,
        },
    }


def generate_conformers(smiles: str, engine_name: str = "auto",
                        n_gen: int = 50, max_confs: int = DEFAULT_MAX_CONFS,
                        e_window_kj: float = DEFAULT_E_WINDOW_KJ,
                        timeout: int = 3600,
                        threads: int | None = None) -> list[dict]:
    """统一入口：engine_name ∈ {auto, etkdg, crest}。

    auto：CREST 可用（本机二进制或 Docker 容器）用 CREST，否则回落
    ETKDG。返回空列表表示失败（调用方提示「构象生成失败/引擎未安装」）。
    threads：CREST 并行线程数（None=runtime_config.crest_threads()，默认 24）。
    """
    name = engine_name or "auto"
    if name == "crest":
        if crest_mode() is None:
            return []
        try:
            xyz = engine.embed_monomer_xyz(smiles)
        except Exception:
            return []
        return generate_conformers_crest(xyz, max_confs, e_window_kj, timeout,
                                          threads=threads)
    if name == "auto":
        if crest_mode() is not None:
            try:
                xyz = engine.embed_monomer_xyz(smiles)
            except Exception:
                return generate_conformers_etkdg(
                    smiles, n_gen, max_confs, e_window_kj)
            crest_out = generate_conformers_crest(
                xyz, max_confs, e_window_kj, timeout, threads=threads)
            if crest_out:
                return crest_out
        return generate_conformers_etkdg(smiles, n_gen, max_confs, e_window_kj)
    # etkdg（默认）
    return generate_conformers_etkdg(smiles, n_gen, max_confs, e_window_kj)


def conformer_cache_key(smiles: str, engine_name: str, n_gen: int,
                        max_confs: int, e_window_kj: float) -> str:
    """构象结果缓存 key（落盘 data/dft_artifacts/conformers/<key>.json）。"""
    payload = f"{smiles}|{engine_name}|{n_gen}|{max_confs}|{e_window_kj}"
    return hashlib.sha1(payload.encode()).hexdigest()[:20]


def load_cached_conformers(smiles: str, engine_name: str, n_gen: int,
                           max_confs: int, e_window_kj: float) -> list[dict] | None:
    """读缓存；不存在或损坏返回 None。"""
    key = conformer_cache_key(smiles, engine_name, n_gen, max_confs,
                              e_window_kj)
    path = runtime_config.user_data_root() / "dft_artifacts" / "conformers" \
        / f"{key}.json"
    try:
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list) and all("xyz" in c for c in data):
            return data
    except Exception:
        return None
    return None


def save_cached_conformers(smiles: str, engine_name: str, n_gen: int,
                           max_confs: int, e_window_kj: float,
                           conformers: list[dict]) -> None:
    """写缓存（尽力而为，失败不影响主流程）。"""
    key = conformer_cache_key(smiles, engine_name, n_gen, max_confs,
                              e_window_kj)
    path = runtime_config.user_data_root() / "dft_artifacts" / "conformers" \
        / f"{key}.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(conformers, ensure_ascii=False),
                        encoding="utf-8")
    except Exception:
        pass
