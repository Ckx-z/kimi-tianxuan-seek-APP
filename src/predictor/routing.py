"""双树模型路由（D22 上线，D23 修订为 routed_strict）：按输入单体是否在训练池中选择树模型。

路由规则（routed_strict，D23）：
- 醛和胺均未见于训练池（双未见）→ tree_v4_noTE（频率降权 + 弱正则，双留出外推最强）
- 其余（双已见 / 一新一熟）→ tree_v4（TE 统计先验有效，LOGO/池内场景最强）

修订依据（exp_008 复盘，D23）：对照策略 routed_strict 在协议 A/B 全部分桶上不劣于
原规定键（任一未见即走 noTE）——混合桶 PR-AUC +0.024（协议 A，3 种子逐一胜）/
+0.031（协议 B 胺已见桶），单侧 TE 在"一新一熟"查询上仍有强信号；唯一代价是
协议 A 混合桶 MAE 小幅回退 −0.0075（约为 PR-AUC 收益的 1/3），列为 D23 复盘点。

路由键来自 models/monomer_pool.json（由训练数据生成，scripts/stage12_train_noTE.py），
加载一次后常驻内存，避免每次预测读 CSV。

打分理由（SHAP 归因）跟随实际路由的模型：tree_v4 走 te_rates 填充路径，
tree_v4_noTE 无 te_rates 走原 v3 路径，两者均已被 attribution.py 支持。
"""

from __future__ import annotations

import json
from pathlib import Path

from .tree_model import MODELS_DIR, TreeFilmPredictor

# 路由资产默认路径（stage15 起切换为 5 种子 bagging 集成，D27：
# 预测输出 mean ± std，std 作为认知不确定度；单模型 pkl 保留可显式回退）
POOL_MODEL_PATH = MODELS_DIR / "tree_v4_ens.pkl"
EXTRAP_MODEL_PATH = MODELS_DIR / "tree_v4_noTE_ens.pkl"
MONOMER_POOL_PATH = MODELS_DIR / "monomer_pool.json"

# 路由键
ROUTE_IN_POOL = "in_pool"
ROUTE_ALD_UNSEEN = "ald_unseen"
ROUTE_AMINE_UNSEEN = "amine_unseen"
ROUTE_BOTH_UNSEEN = "both_unseen"

# 路由原因（中文，供 App 前端展示）
_ROUTE_REASONS_ZH = {
    ROUTE_IN_POOL: "已知单体组合 → tree_v4 集成（含历史先验，5 种子 bagging）",
    ROUTE_ALD_UNSEEN: "含未见单体（醛）但非双未见 → tree_v4 集成（沿用池内模型）",
    ROUTE_AMINE_UNSEEN: "含未见单体（胺）但非双未见 → tree_v4 集成（沿用池内模型）",
    ROUTE_BOTH_UNSEEN: "双未见单体（醛/胺均未见）→ tree_v4_noTE 集成（外推模式）",
}


class MonomerPool:
    """训练单体池：判断醛/胺 SMILES 是否在训练集中出现过。"""

    def __init__(self, aldehydes, amines):
        self.aldehydes = frozenset(aldehydes)
        self.amines = frozenset(amines)

    @classmethod
    def load(cls, path: str | Path = MONOMER_POOL_PATH) -> "MonomerPool":
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(
                f"找不到单体池文件：{path}。请运行 scripts/stage12_train_noTE.py 生成。")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return cls(data["aldehydes"], data["amines"])

    def ald_seen(self, smiles: str) -> bool:
        return smiles in self.aldehydes

    def amine_seen(self, smiles: str) -> bool:
        return smiles in self.amines

    def route_key(self, ald_smiles: str, amine_smiles: str) -> str:
        """返回路由键：in_pool / ald_unseen / amine_unseen / both_unseen。"""
        ald = self.ald_seen(ald_smiles)
        amine = self.amine_seen(amine_smiles)
        if ald and amine:
            return ROUTE_IN_POOL
        if not ald and not amine:
            return ROUTE_BOTH_UNSEEN
        return ROUTE_ALD_UNSEEN if not ald else ROUTE_AMINE_UNSEEN


class RoutedTreePredictor:
    """双树模型路由器（routed_strict，D23）：双未见 tree_v4_noTE / 其余 tree_v4。"""

    def __init__(self,
                 pool_model_path: str | Path = POOL_MODEL_PATH,
                 extrap_model_path: str | Path = EXTRAP_MODEL_PATH,
                 monomer_pool_path: str | Path = MONOMER_POOL_PATH):
        self.pool_model = TreeFilmPredictor(model_path=pool_model_path)
        self.extrap_model = TreeFilmPredictor(model_path=extrap_model_path)
        self.monomer_pool_path = Path(monomer_pool_path)
        self.pool: MonomerPool | None = None

    def load(self) -> None:
        """加载两个模型与单体池。"""
        self.pool_model.load()
        self.extrap_model.load()
        self.pool = MonomerPool.load(self.monomer_pool_path)

    def route_for(self, ald_smiles: str, amine_smiles: str) -> tuple[TreeFilmPredictor, str, str]:
        """返回 (实际使用的 TreeFilmPredictor, 路由键, 路由原因中文)。

        routed_strict（D23）：仅双未见（ROUTE_BOTH_UNSEEN）走外推臂 tree_v4_noTE，
        其余（双已见 / 一新一熟）均走池内臂 tree_v4。
        """
        if self.pool is None:
            self.load()
        key = self.pool.route_key(ald_smiles, amine_smiles)
        model = self.extrap_model if key == ROUTE_BOTH_UNSEEN else self.pool_model
        return model, key, _ROUTE_REASONS_ZH[key]

    def predict_with_info(self, ald_smiles: str, amine_smiles: str) -> dict:
        """路由预测，返回概率均值 + 成员 std（认知不确定度）+ 实际使用模型 + 路由原因。"""
        model, key, reason = self.route_for(ald_smiles, amine_smiles)
        prob, std = model.predict_single_with_std(ald_smiles, amine_smiles)
        return {
            "probability": prob,
            "std": std,
            "model_name": model.model_path.stem,
            "route": key,
            "route_reason": reason,
            "ald_seen": self.pool.ald_seen(ald_smiles),
            "amine_seen": self.pool.amine_seen(amine_smiles),
        }

    def predict_single(self, ald_smiles: str, amine_smiles: str) -> float:
        """路由预测概率（与 TreeFilmPredictor.predict_single 同签名）。"""
        return self.predict_with_info(ald_smiles, amine_smiles)["probability"]
