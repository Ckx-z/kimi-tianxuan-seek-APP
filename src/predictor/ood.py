"""OOD（分布外）检测：三级制 none / warning / out（D27，P2）。

背景（exp_011 / D26）：真实回测暴露 OOD 输出缺口——H 系酰肼单体（醛+酰肼成
腙键而非醛-胺成亚胺键）模型无法可靠打分，旧 GNN 当年还知道标"模型不适合非标准
官能团"。App 必须能识别并明示这类情况，而不是硬报一个数。

三类检测（按优先级合并，等级取最高）：

a. **官能团适配性（out）**：
   - 胺侧匹配非标准成键基团（酰肼/肼/羟胺等）→ out
     （对应 H3 案例：醛+酰肼成腙键，不在亚胺 COF 训练分布内）；
   - 胺侧未检测到伯胺/仲胺 → out；
   - 醛侧未检测到醛基 C(=O)H → out。

b. **单体新颖性（warning）**：醛/胺双未见于训练池（与路由臂联动：
   双未见走 tree_v4_noTE 外推臂）→ warning"外推模式，打分可信度降低"。

c. **特征区域漂移（warning）**：关键特征（MW / 芳香环数 / 3D 体积等，
   见 models/feature_envelope.json）超出训练 5%–95% 包络的比例 > 10%
   → warning"单体尺寸/骨架超出训练分布"（对应 fold3 诊断，exp_007）。

输出结构：
    {"level": "none" | "warning" | "out", "reasons": [中文原因...],
     "checks": {各检测器明细}}
"""

from __future__ import annotations

import json
from pathlib import Path

from rdkit import Chem

try:
    from src import runtime_config
except ImportError:
    import runtime_config  # type: ignore

PROJECT_ROOT = runtime_config.resource_root()
ENVELOPE_PATH = PROJECT_ROOT / "models" / "feature_envelope.json"

# 等级常量
LEVEL_NONE = "none"
LEVEL_WARNING = "warning"
LEVEL_OUT = "out"
_LEVEL_RANK = {LEVEL_NONE: 0, LEVEL_WARNING: 1, LEVEL_OUT: 2}

# ---------------------------------------------------------------- SMARTS 模式

# 醛基：C(=O)H（甲酰基），羧酸/酯/酰胺的羰基碳无 H，不匹配
_SMARTS_ALDEHYDE = "[CX3H](=O)"
# 伯胺/仲胺：排除酰胺/酰肼（N 直连 C=O）、羟胺（N-O）、硝基/亚硝基（N=O）
_SMARTS_PRIMARY_AMINE = "[NX3H2;!$(N[C,S]=O);!$(NO);!$(N=O)]"
_SMARTS_SECONDARY_AMINE = "[NX3H1;!$(N[C,S]=O);!$(NO);!$(N=O)]([#6])[#6]"
# 非标准成键基团（命中即 out）——醛+这类基团不成亚胺键
_SMARTS_HYDRAZIDE = "[CX3](=O)[NX3;H1,H2]"       # 酰肼 C(=O)NH-NH2（腙键前体，H 系案例）
_SMARTS_HYDRAZINE = "[NX3;H1,H2][NX3;H1,H2]"     # 肼 / 酰肼的 N-N 骨架
_SMARTS_HYDROXYLAMINE = "[NX3][OX2H1]"           # 羟胺 N-OH（成肟而非亚胺）

_PATTERNS = {
    name: Chem.MolFromSmarts(s)
    for name, s in {
        "aldehyde": _SMARTS_ALDEHYDE,
        "primary_amine": _SMARTS_PRIMARY_AMINE,
        "secondary_amine": _SMARTS_SECONDARY_AMINE,
        "hydrazide": _SMARTS_HYDRAZIDE,
        "hydrazine": _SMARTS_HYDRAZINE,
        "hydroxylamine": _SMARTS_HYDROXYLAMINE,
    }.items()
}

# 单体侧中文名（用于原因文案）
_SIDE_ZH = {"aldehyde": "醛", "amine": "胺"}


# ---------------------------------------------------------------- 检测器

def check_functional_groups(ald_smiles: str, amine_smiles: str) -> dict:
    """官能团适配性检测（a 类）。任一命中 → out。"""
    reasons = []
    details = {}

    ald_mol = Chem.MolFromSmiles(ald_smiles)
    if ald_mol is None:
        reasons.append("醛单体 SMILES 无法解析，无法判断官能团适配性")
        details["aldehyde"] = "unparsable"
    else:
        n_ald = len(ald_mol.GetSubstructMatches(_PATTERNS["aldehyde"]))
        details["aldehyde"] = {"n_aldehyde_groups": n_ald}
        if n_ald == 0:
            reasons.append("醛侧未检测到醛基 C(=O)H：模型按醛-胺缩聚（亚胺键）场景训练，"
                           "对非醛单体的打分不适用")

    am_mol = Chem.MolFromSmiles(amine_smiles)
    if am_mol is None:
        reasons.append("胺单体 SMILES 无法解析，无法判断官能团适配性")
        details["amine"] = "unparsable"
    else:
        n_pri = len(am_mol.GetSubstructMatches(_PATTERNS["primary_amine"]))
        n_sec = len(am_mol.GetSubstructMatches(_PATTERNS["secondary_amine"]))
        is_hydrazide = am_mol.HasSubstructMatch(_PATTERNS["hydrazide"])
        is_hydrazine = am_mol.HasSubstructMatch(_PATTERNS["hydrazine"])
        is_hydroxylamine = am_mol.HasSubstructMatch(_PATTERNS["hydroxylamine"])
        details["amine"] = {"n_primary_amine": n_pri, "n_secondary_amine": n_sec,
                            "hydrazide": is_hydrazide, "hydrazine": is_hydrazine,
                            "hydroxylamine": is_hydroxylamine}
        # 非标准成键基团优先判定（即使同时含游离 NH2 也不可靠）
        if is_hydrazide or is_hydrazine:
            reasons.append("胺侧为酰肼/肼类非标准官能团：醛+酰肼成腙键而非亚胺键，"
                           "模型按醛-胺缩聚场景训练，不适用（对应 H 系单体案例）")
        elif is_hydroxylamine:
            reasons.append("胺侧为羟胺类非标准官能团：醛+羟胺成肟而非亚胺键，"
                           "模型按醛-胺缩聚场景训练，不适用")
        elif n_pri + n_sec == 0:
            reasons.append("胺侧未检测到伯胺/仲胺基团：模型按醛-胺缩聚场景训练，"
                           "对非胺单体的打分不适用")

    return {
        "level": LEVEL_OUT if reasons else LEVEL_NONE,
        "reasons": reasons,
        "details": details,
    }


def check_novelty(ald_smiles: str, amine_smiles: str, pool) -> dict:
    """单体新颖性检测（b 类）。双未见 → warning（与路由臂联动）。

    Args:
        pool: MonomerPool 实例；为 None 时跳过本检测。
    """
    if pool is None:
        return {"level": LEVEL_NONE, "reasons": [], "details": "no_pool"}
    ald_seen = pool.ald_seen(ald_smiles)
    amine_seen = pool.amine_seen(amine_smiles)
    details = {"ald_seen": ald_seen, "amine_seen": amine_seen}
    if not ald_seen and not amine_seen:
        return {
            "level": LEVEL_WARNING,
            "reasons": ["醛/胺单体均未在训练集中出现过（双未见）→ 外推模式，"
                        "打分可信度降低（走 noTE 外推臂）"],
            "details": details,
        }
    return {"level": LEVEL_NONE, "reasons": [], "details": details}


def load_envelope(path: str | Path = ENVELOPE_PATH) -> dict | None:
    """加载训练特征包络；文件缺失时返回 None（跳过 c 类检测，优雅降级）。"""
    path = Path(path)
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def check_feature_drift(ald_smiles: str, amine_smiles: str,
                        envelope: dict | None = None) -> dict:
    """特征区域漂移检测（c 类）。关键特征超包络比例 > 阈值 → warning。

    对应 fold3 诊断（exp_007）：区域漂移大芳香单体是双留出最难折，
    模型对这类样本全线不可信。
    """
    if envelope is None:
        envelope = load_envelope()
    if envelope is None:
        return {"level": LEVEL_NONE, "reasons": [], "details": "no_envelope"}

    # 延迟导入（避免 predictor 包加载时的重依赖）
    import sys
    src_dir = Path(__file__).resolve().parent.parent
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))
    from features.descriptors import compute_pair_features

    feats = compute_pair_features(ald_smiles, amine_smiles,
                                  use_rules=False, use_interaction=False, use_3d=True)
    threshold = envelope.get("out_ratio_threshold", 0.10)
    out_features = []
    checked = 0
    for name, env in envelope["features"].items():
        v = feats.get(name)
        if v is None:
            continue
        checked += 1
        if v < env["p05"] or v > env["p95"]:
            out_features.append({
                "feature": name, "value": float(v),
                "p05": env["p05"], "p95": env["p95"],
            })
    ratio = len(out_features) / checked if checked else 0.0
    details = {"n_checked": checked, "n_out": len(out_features),
               "out_ratio": ratio, "threshold": threshold,
               "out_features": out_features}
    if ratio > threshold:
        names = "、".join(f["feature"] for f in out_features[:4])
        return {
            "level": LEVEL_WARNING,
            "reasons": [f"单体尺寸/骨架超出训练分布（{len(out_features)}/{checked} 项关键特征"
                        f"超出训练 5%–95% 包络：{names}），打分可信度降低"
                        f"（对应 fold3 区域漂移诊断）"],
            "details": details,
        }
    return {"level": LEVEL_NONE, "reasons": [], "details": details}


def check_ood(ald_smiles: str, amine_smiles: str, pool=None,
              envelope: dict | None = None) -> dict:
    """OOD 综合检测：三类合并，等级取最高，原因合并（中文）。"""
    checks = {
        "functional_group": check_functional_groups(ald_smiles, amine_smiles),
        "novelty": check_novelty(ald_smiles, amine_smiles, pool),
        "feature_drift": check_feature_drift(ald_smiles, amine_smiles, envelope),
    }
    level = LEVEL_NONE
    reasons = []
    for c in checks.values():
        if _LEVEL_RANK[c["level"]] > _LEVEL_RANK[level]:
            level = c["level"]
        reasons.extend(c["reasons"])
    return {"level": level, "reasons": reasons, "checks": checks}
