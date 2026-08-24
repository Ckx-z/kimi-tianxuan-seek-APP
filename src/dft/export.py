"""DFT 结果导出为量化软件输入文件（Gaussian .gjf / ORCA .inp）。

纯函数模块：从复合物优化后 xyz 文本生成输入文件内容，供
GET /api/dft/jobs/{id}/export 下载端点与测试复用。

- Gaussian：%nprocshared/%mem 头 + `# opt b3lyp/6-31g(d) scrf=smd` 路由行
  + 标题段注释（说明默认电荷/自旋多重度 0 1，需用户自行检查）+ 坐标 + 空行结尾
- ORCA：`! B3LYP def2-SVP OPT` 主输入行 + `#` 注释 + `* xyz 0 1 ... *` 坐标块
"""

from __future__ import annotations

# 支持导出的格式档位
FORMATS = ("gaussian", "orca")


class DftExportError(ValueError):
    """导出失败（xyz 无法解析 / 未知格式），message 为中文原因。"""


def parse_xyz_coords(xyz: str) -> list[str]:
    """解析 xyz 文本，返回坐标行列表（原子符号 + x y z）。

    校验首行原子数与实际坐标行数一致；不一致抛 DftExportError。
    """
    lines = [ln.rstrip() for ln in (xyz or "").splitlines()]
    # 跳过可能的前导空行
    while lines and not lines[0].strip():
        lines.pop(0)
    if len(lines) < 2:
        raise DftExportError("xyz 内容不完整：缺少原子数行或坐标区")
    try:
        n_atoms = int(lines[0].strip())
    except ValueError:
        raise DftExportError(f"xyz 首行应为原子数，收到 {lines[0].strip()!r}")
    coords = [ln.strip() for ln in lines[2:] if ln.strip()]
    if len(coords) != n_atoms:
        raise DftExportError(
            f"xyz 原子数（{n_atoms}）与坐标行数（{len(coords)}）不一致")
    for ln in coords:
        parts = ln.split()
        if len(parts) < 4:
            raise DftExportError(f"坐标行格式不完整：{ln!r}")
    return coords


# Gaussian 标题段注释：说明来源与电荷/自旋多重度需自行检查
_GAUSSIAN_TITLE = (
    "COF monomer complex geometry from xTB ({source}) / 由 COF 科研助手导出。\n"
    "默认电荷 0、自旋多重度 1（0 1）；提交前请自行检查电荷与自旋多重度，\n"
    "必要时调整基组/溶剂模型（当前路由行为 b3lyp/6-31g(d) scrf=smd）。")

_ORCA_HEADER_COMMENT = (
    "# COF monomer complex geometry from xTB ({source}) / 由 COF 科研助手导出\n"
    "# 默认电荷 0、自旋多重度 1；提交前请自行检查电荷与自旋多重度")


def build_gaussian_input(xyz: str, source: str = "gfn2",
                         nproc: int = 8, mem: str = "8GB") -> str:
    """生成 Gaussian 输入（.gjf）：link0 头 + 路由行 + 标题 + 0 1 + 坐标。"""
    coords = parse_xyz_coords(xyz)
    parts = [
        f"%nprocshared={nproc}",
        f"%mem={mem}",
        "# opt b3lyp/6-31g(d) scrf=smd",
        "",
        _GAUSSIAN_TITLE.format(source=source),
        "",
        "0 1",
        *coords,
        "",  # Gaussian 输入需以空行结尾
    ]
    return "\n".join(parts)


def build_orca_input(xyz: str, source: str = "gfn2") -> str:
    """生成 ORCA 输入（.inp）：主输入行 + 注释 + xyz 坐标块。"""
    coords = parse_xyz_coords(xyz)
    parts = [
        _ORCA_HEADER_COMMENT.format(source=source),
        "! B3LYP def2-SVP OPT",
        "",
        "* xyz 0 1",
        *coords,
        "*",
        "",
    ]
    return "\n".join(parts)


def build_export(fmt: str, xyz: str, source: str = "gfn2") -> str:
    """按格式档位生成输入文件内容；未知档位抛 DftExportError。"""
    if fmt == "gaussian":
        return build_gaussian_input(xyz, source=source)
    if fmt == "orca":
        return build_orca_input(xyz, source=source)
    raise DftExportError(
        f"未知导出格式：{fmt!r}（可选 gaussian / orca）")


def export_filename(fmt: str, method: str = "") -> str:
    """导出下载文件名（中文，content-disposition 需 RFC 5987 编码）。"""
    stem = {"gaussian": "gaussian输入", "orca": "orca输入"}.get(fmt, fmt)
    ext = {"gaussian": "gjf", "orca": "inp"}.get(fmt, "txt")
    suffix = f"_{method}" if method else ""
    return f"COF复合物_{stem}{suffix}.{ext}"
