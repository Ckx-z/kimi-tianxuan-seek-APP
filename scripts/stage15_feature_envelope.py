"""阶段 15（P2）：训练特征 5%–95% 包络，供 OOD 区域漂移检测使用。

背景（exp_007 fold3 诊断）：区域漂移型大芳香单体在双留出 fold3 上全线坍缩
（树/GNN/embedding ~0.36），模型对这类样本不可信——需要在预测时识别并提示。

本脚本：从 stage11 特征缓存（全量训练样本 X_base）提取关键几何/骨架特征的
5%–95% 分位包络，保存 models/feature_envelope.json（入库，自描述）。
预测侧（src/predictor/ood.py）检查样本关键特征超出包络的比例，
超过阈值（默认 10%）→ OOD warning。

用法：
    .venv\\Scripts\\python.exe scripts/stage15_feature_envelope.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import joblib
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from stage11_dual_holdout import load_v3, load_xy  # noqa: E402

OUT_JSON = PROJECT_ROOT / "models" / "feature_envelope.json"

# 关键特征：单体尺寸 / 芳香骨架 / 3D 体积（fold3 漂移的主要形态）
# 均为单体级特征（醛/胺对称），不含交互/规则特征
KEY_FEATURES = [
    "ald_mw", "amine_mw",
    "ald_mw_per_site", "amine_mw_per_site",
    "ald_n_aromatic_rings", "amine_n_aromatic_rings",
    "ald_n_rings", "amine_n_rings",
    "ald_tpsa", "amine_tpsa",
    "ald_aromatic_frac", "amine_aromatic_frac",
    "ald_3d_mol_volume", "amine_3d_mol_volume",
    "ald_3d_radius_gyration", "amine_3d_radius_gyration",
]


def main() -> None:
    X_base, y, df = load_xy()
    _, _, cols = load_v3()
    idx = {c: cols.index(c) for c in KEY_FEATURES}
    envelope = {}
    for name, i in idx.items():
        v = X_base[:, i]
        envelope[name] = {
            "p05": float(np.percentile(v, 5)),
            "p95": float(np.percentile(v, 95)),
            "min": float(v.min()),
            "max": float(v.max()),
        }
    payload = {
        "note": "训练特征 5%–95% 包络（stage11 全量缓存），OOD 区域漂移检测键；"
                "样本关键特征超出包络比例 > 10% → warning（D27）",
        "source": "data/interim/stage11_xy_cache.pkl（v5_train_stage1_cond_filled 过滤 hard_rule 后）",
        "n_samples": int(X_base.shape[0]),
        "lower_q": 5, "upper_q": 95,
        "out_ratio_threshold": 0.10,
        "features": envelope,
    }
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"已保存: {OUT_JSON}（{len(envelope)} 个特征包络）")


if __name__ == "__main__":
    main()
