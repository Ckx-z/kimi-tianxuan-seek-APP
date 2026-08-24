#!/usr/bin/env python
"""下载并组装 vendor/xtb（Windows x64，xtb 6.7.0 官方静态构建 + Intel OpenMP 运行库）。

用法（项目根目录）：
    python scripts/fetch_xtb.py

产物（不入库，.gitignore 已忽略）：
    vendor/xtb/bin/xtb.exe
    vendor/xtb/bin/libiomp5md.dll   （Intel OpenMP redistributable）
    vendor/xtb/share/xtb/           （GFN 参数文件，运行时需设 XTBPATH 指向它）
    vendor/xtb/share/licenses/      （LGPL-3.0 许可证文本，合规要求随分发保留）

来源：
  - xtb:    https://github.com/grimme-lab/xtb/releases/download/v6.7.0/xtb-6.7.0-Windows-x86_64.zip
  - OpenMP: conda-forge intel-openmp（清华镜像），仅取 libiomp5md.dll
"""
from __future__ import annotations

import io
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor" / "xtb"
TMP = ROOT / ".tmp_xtb"

XTB_URLS = [
    "https://ghfast.top/https://github.com/grimme-lab/xtb/releases/download/v6.7.0/xtb-6.7.0-Windows-x86_64.zip",
    "https://gh-proxy.com/https://github.com/grimme-lab/xtb/releases/download/v6.7.0/xtb-6.7.0-Windows-x86_64.zip",
    "https://github.com/grimme-lab/xtb/releases/download/v6.7.0/xtb-6.7.0-Windows-x86_64.zip",
]
# intel-openmp 2026.1.0 win-64（清华镜像）；如文件名变动，到
# https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/conda-forge/win-64/ 查最新
OMP_URLS = [
    "https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/conda-forge/win-64/intel-openmp-2026.1.0-h57928b3_246.conda",
    "https://conda.anaconda.org/conda-forge/win-64/intel-openmp-2026.1.0-h57928b3_246.conda",
]


def _download(urls: list[str], dest: Path, min_size: int) -> None:
    for url in urls:
        try:
            print(f"下载 {url}")
            req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
            with urllib.request.urlopen(req, timeout=300) as r, open(dest, "wb") as f:
                shutil.copyfileobj(r, f)
            if dest.stat().st_size >= min_size:
                print(f"  -> {dest.name} {dest.stat().st_size / 1e6:.1f} MB")
                return
            print("  文件过小，尝试下一镜像")
        except Exception as e:  # noqa: BLE001
            print(f"  失败: {e}")
    sys.exit(f"所有镜像均失败: {dest.name}")


def main() -> None:
    if (VENDOR / "bin" / "xtb.exe").is_file() and (VENDOR / "bin" / "libiomp5md.dll").is_file():
        print("vendor/xtb 已就绪，跳过。如需重建请先删除该目录。")
        return
    TMP.mkdir(exist_ok=True)

    # 1. xtb 官方 Windows 包
    xtb_zip = TMP / "xtb-win.zip"
    if not xtb_zip.is_file():
        _download(XTB_URLS, xtb_zip, min_size=30_000_000)
    with zipfile.ZipFile(xtb_zip) as z:
        for member in z.namelist():
            rel = Path(member)
            if not member.startswith("xtb-6.7.0/") or member.endswith("/"):
                continue
            inner = rel.relative_to("xtb-6.7.0")
            if inner.parts[0] == "bin" and inner.name == "xtb.exe":
                target = VENDOR / "bin" / "xtb.exe"
            elif inner.parts[0] == "share" and (inner.parts[1] in ("xtb", "licenses") if len(inner.parts) > 1 else False):
                target = VENDOR / "share" / Path(*inner.parts[1:])
            else:
                continue  # include/lib 等开发文件不需要
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(member) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)

    # 2. Intel OpenMP 运行库（.conda = zip 外层 + pkg zst 内层；直接用 zipfile 取不行时用 conda-package-handling）
    omp_pkg = TMP / "intel-openmp.conda"
    dll_target = VENDOR / "bin" / "libiomp5md.dll"
    if not dll_target.is_file():
        if not omp_pkg.is_file():
            _download(OMP_URLS, omp_pkg, min_size=10_000_000)
        _extract_iomp(omp_pkg, dll_target)

    print(f"完成: {VENDOR}")
    print(f"  xtb.exe           {(VENDOR / 'bin' / 'xtb.exe').stat().st_size / 1e6:.1f} MB")
    print(f"  libiomp5md.dll    {dll_target.stat().st_size / 1e6:.1f} MB")


def _extract_iomp(conda_pkg: Path, dll_target: Path) -> None:
    """从 .conda 包提取 libiomp5md.dll（外层 zip 内的 pkg-*.tar.zst）。"""
    try:
        import zstandard  # type: ignore
    except ImportError:
        sys.exit("需要 zstandard: pip install zstandard")
    import tarfile

    with zipfile.ZipFile(conda_pkg) as z:
        pkg_member = next(n for n in z.namelist() if n.startswith("pkg-") and n.endswith(".tar.zst"))
        dctx = zstandard.ZstdDecompressor()
        raw = dctx.stream_reader(io.BytesIO(z.read(pkg_member)))
        with tarfile.open(fileobj=raw, mode="r|") as tar:
            for m in tar:
                if m.name.endswith("libiomp5md.dll"):
                    dll_target.parent.mkdir(parents=True, exist_ok=True)
                    with tar.extractfile(m) as src, open(dll_target, "wb") as dst:  # type: ignore[arg-type]
                        shutil.copyfileobj(src, dst)
                    return
    sys.exit("包内未找到 libiomp5md.dll")


if __name__ == "__main__":
    main()
