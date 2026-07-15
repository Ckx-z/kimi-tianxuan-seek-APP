r"""数据导入工具：从旧项目只读复制数据到新工作台 data/raw/。

约束：绝不修改旧项目 C:\Users\ckx\Desktop\tianxuan seek 中的任何文件。
"""

from __future__ import annotations

import shutil
from pathlib import Path

# 旧项目路径（只读源）
OLD_PROJECT_ROOT = Path(r"C:\Users\ckx\Desktop\tianxuan seek")

# 新工作台数据目录（目标）
NEW_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_RAW_DIR = NEW_PROJECT_ROOT / "data" / "raw"

# 数据文件映射：旧项目相对路径 -> 新目标文件名
DATA_FILES = {
    # 必需
    "data/processed/v5_train_stage1.csv": "v5_train_stage1.csv",
    "data/processed/merged_monomer_pool.csv": "merged_monomer_pool.csv",
    # 可选（阶段 4/5 可能用到）
    "data/processed/v5_train_stage1_aug_v2.csv": "v5_train_stage1_aug_v2.csv",
}


def copy_file(src: Path, dst: Path) -> dict:
    """复制单个文件，返回元信息。"""
    if not src.exists():
        raise FileNotFoundError(f"源文件不存在：{src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return {
        "src": str(src),
        "dst": str(dst),
        "size_bytes": dst.stat().st_size,
    }


def import_data(include_optional: bool = True) -> list[dict]:
    """将必需（和可选）数据文件从旧项目复制到新工作台。

    Args:
        include_optional: 是否复制可选文件（增广训练集）。

    Returns:
        复制日志列表，每项包含 src、dst、size_bytes。
    """
    files = dict(DATA_FILES)
    if not include_optional:
        # 只保留必需文件
        files = {
            k: v
            for k, v in files.items()
            if k in {
                "data/processed/v5_train_stage1.csv",
                "data/processed/merged_monomer_pool.csv",
            }
        }

    logs: list[dict] = []
    for rel_src, dst_name in files.items():
        src = OLD_PROJECT_ROOT / rel_src
        dst = DATA_RAW_DIR / dst_name
        log = copy_file(src, dst)
        logs.append(log)
    return logs


def main():
    """命令行入口。"""
    print(f"旧项目（只读源）: {OLD_PROJECT_ROOT}")
    print(f"新工作台目标: {DATA_RAW_DIR}")
    print("-" * 60)

    logs = import_data(include_optional=True)
    for log in logs:
        size_kb = log["size_bytes"] / 1024
        print(f"✅ 已复制: {log['src']}")
        print(f"   -> {log['dst']} ({size_kb:.1f} KB)")

    print("-" * 60)
    print(f"共复制 {len(logs)} 个文件。")
    print("注意：以上操作仅复制数据，未修改旧项目任何文件。")


if __name__ == "__main__":
    main()
