"""醛-胺单体交互特征。

模拟旧项目 GNN 中的 Hadamard 积式双向交互：
    f_interact = f_aldehyde ⊙ f_amine
同时补充差值和比值特征，让树模型能学到"同一个胺和不同醛组合时的差异"。

这些特征专门用来回答：两个单体配对时，哪一方的哪些结构属性在主导成膜。
"""

from __future__ import annotations

from typing import Dict


# 参与交互的单体特征键。
# 选择对 COF 成膜有明确化学意义的属性：尺寸、芳香性、柔性、极性、链接类型。
INTERACTION_KEYS = [
    # 绝对量
    "mw",
    "n_heavy",
    "n_aromatic_rings",
    "n_rings",
    "n_rotatable",
    "tpsa",
    "logp",
    # 归一化到反应位点（更公平比较 C2 vs C3）
    "mw_per_site",
    "n_aromatic_rings_per_site",
    "n_rings_per_site",
    "n_heavy_per_site",
    "tpsa_per_site",
    # 链接/结构信号
    "aromatic_frac",
    "ring_frac",
    "n_acetylene",
    "has_acetylene",
    "has_heterocycle",
]


def _safe_ratio(a: float, b: float, eps: float = 1e-6) -> float:
    """安全除法。"""
    if b == 0:
        return 0.0
    return a / (b + eps)


def compute_interaction_features(ald_features: Dict[str, float],
                                  amine_features: Dict[str, float]) -> Dict[str, float]:
    """计算醛-胺交互特征。

    Args:
        ald_features: 醛单体特征字典
        amine_features: 胺单体特征字典

    Returns:
        交互特征字典，包含 Hadamard 积、差值、比值三种形式。
    """
    result = {}
    for key in INTERACTION_KEYS:
        a = ald_features.get(key, 0.0)
        b = amine_features.get(key, 0.0)

        # Hadamard 积：模拟 GNN 中逐元素相乘的交互
        result[f"hadamard_{key}"] = a * b

        # 差值：捕捉两者不对称性
        result[f"diff_{key}"] = a - b

        # 比值：捕捉相对大小
        result[f"ratio_{key}"] = _safe_ratio(a, b)

    return result
