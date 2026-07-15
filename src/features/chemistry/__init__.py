# 化学信息处理模块（已从旧项目复制，适配新工作台路径）
from .linker_analyzer import (
    has_acetylene, count_acetylene, classify_linker_type, pair_linker_type,
    is_functionally_symmetric, has_heterocycle, count_aromatic_rings,
    compute_monomer_descriptors, compute_pair_descriptors,
    compute_pair_descriptor_vector,
)
from .conformer import compute_3d_descriptors, DESCRIPTOR_NAMES
from .dimer import compute_dimer_3d, DIMER_DESCRIPTOR_NAMES
from .hard_rules import get_rule_vector, RULE_DIM

__all__ = [
    "has_acetylene",
    "count_acetylene",
    "classify_linker_type",
    "pair_linker_type",
    "is_functionally_symmetric",
    "has_heterocycle",
    "count_aromatic_rings",
    "compute_monomer_descriptors",
    "compute_pair_descriptors",
    "compute_pair_descriptor_vector",
    "compute_3d_descriptors",
    "DESCRIPTOR_NAMES",
    "compute_dimer_3d",
    "DIMER_DESCRIPTOR_NAMES",
    "get_rule_vector",
    "RULE_DIM",
]
