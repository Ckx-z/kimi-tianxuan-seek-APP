"""v4 模型顶层组装 — encoder -> attention -> pooling -> 3D branch -> FilmHead。

与 v3 的区别:
- 更小 (hidden_dim=128, layers=3)
- 无 ConditionHead
- 支持 batch_size > 1
- 可选 3D 构象描述符分支
"""
from __future__ import annotations

import torch
import torch.nn as nn
from torch_geometric.data import Data

from src.screening.gnn_v4.encoder import SiameseEncoder
from src.screening.gnn_v4.attention import CrossGraphAttention
from src.screening.gnn_v4.pooling import PairPooling
from src.screening.gnn_v4.heads import FilmHead


class ConformerBranch(nn.Module):
    """3D 构象描述符分支 — 单体(20维) + 二聚体(10维) → 64维嵌入。

    分开归一化：单体描述符用 monomer scaler，二聚体用 dimer scaler。
    """

    def __init__(self, monomer_dim: int = 10, dimer_dim: int = 10,
                 hidden_dim: int = 32, out_dim: int = 64):
        super().__init__()
        in_dim = monomer_dim * 2 + dimer_dim  # 20 + 10 = 30
        self.monomer_dim = monomer_dim
        self.dimer_dim = dimer_dim
        self.mlp = nn.Sequential(
            nn.Linear(in_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, out_dim),
        )
        self.norm = nn.LayerNorm(out_dim)
        # 归一化参数
        self.register_buffer("monomer_mean", torch.zeros(monomer_dim))
        self.register_buffer("monomer_std", torch.ones(monomer_dim))
        self.register_buffer("dimer_mean", torch.zeros(dimer_dim))
        self.register_buffer("dimer_std", torch.ones(dimer_dim))

    def forward(self, ald_3d: torch.Tensor, amine_3d: torch.Tensor,
                dimer_3d: torch.Tensor | None = None) -> torch.Tensor:
        """ald_3d, amine_3d: [B, 10], dimer_3d: [B, 10] → [B, 64]."""
        ald_norm = (ald_3d - self.monomer_mean) / self.monomer_std
        amine_norm = (amine_3d - self.monomer_mean) / self.monomer_std
        parts = [ald_norm, amine_norm]
        if dimer_3d is not None:
            dimer_norm = (dimer_3d - self.dimer_mean) / self.dimer_std
            parts.append(dimer_norm)
        x = torch.cat(parts, dim=-1)
        return self.norm(self.mlp(x))


class V4Model(nn.Module):
    """v4 成膜预测模型 — 缩小版 GIN+GINE + Cross-Attention + 3D + FilmHead。"""

    def __init__(self, cfg: dict | None = None):
        super().__init__()
        cfg = cfg or {}

        enc_cfg = cfg.get("encoder", {})
        attn_cfg = cfg.get("attention", {})
        pool_cfg = cfg.get("pooling", {})
        head_cfg = cfg.get("heads", {})
        film_cfg = head_cfg.get("film_head", {})
        model_cfg = cfg.get("model", {})

        hidden_dim = enc_cfg.get("hidden_dim", 128)
        self.use_3d = model_cfg.get("use_3d", False)
        monomer_3d_dim = model_cfg.get("monomer_3d_dim", 10)
        dimer_3d_dim = model_cfg.get("dimer_3d_dim", 10)

        self.encoder = SiameseEncoder(
            in_dim=enc_cfg.get("in_dim", 37),
            hidden_dim=hidden_dim,
            num_layers=enc_cfg.get("num_layers", 3),
            dropout=enc_cfg.get("dropout", 0.15),
        )
        self.attention = CrossGraphAttention(
            hidden_dim=attn_cfg.get("hidden_dim", hidden_dim),
            num_heads=attn_cfg.get("num_heads", 4),
            dropout=attn_cfg.get("dropout", 0.15),
        )
        self.pooling = PairPooling(
            hidden_dim=pool_cfg.get("hidden_dim", hidden_dim),
            num_queries=pool_cfg.get("num_queries", 4),
        )

        if self.use_3d:
            self.conformer_branch = ConformerBranch(
                monomer_dim=monomer_3d_dim, dimer_dim=dimer_3d_dim,
                hidden_dim=32, out_dim=64,
            )

        self.use_rules = model_cfg.get("use_rules", True)
        dim_rules = model_cfg.get("dim_rules", 23)
        self.film_head = FilmHead(
            hidden_dim=hidden_dim,
            use_3d=self.use_3d,
            dim_3d=64,
            use_rules=self.use_rules,
            dim_rules=dim_rules,
            dropout=film_cfg.get("dropout", 0.25),
        )

    def set_3d_scaler(self, monomer_mean: list[float], monomer_std: list[float],
                       dimer_mean: list[float] | None = None,
                       dimer_std: list[float] | None = None) -> None:
        """设置单体 + 二聚体 3D 描述符归一化参数。"""
        self.conformer_branch.monomer_mean.copy_(torch.tensor(monomer_mean))
        self.conformer_branch.monomer_std.copy_(torch.tensor(monomer_std))
        if dimer_mean is not None and dimer_std is not None:
            self.conformer_branch.dimer_mean.copy_(torch.tensor(dimer_mean))
            self.conformer_branch.dimer_std.copy_(torch.tensor(dimer_std))

    def forward(self, ald_data: Data, amine_data: Data,
                ald_batch: torch.Tensor, amine_batch: torch.Tensor,
                batch_size: int,
                ald_3d: torch.Tensor | None = None,
                amine_3d: torch.Tensor | None = None,
                dimer_3d: torch.Tensor | None = None,
                rule_vec: torch.Tensor | None = None) -> torch.Tensor:
        ald_emb, amine_emb = self.encoder(ald_data, amine_data)
        ald_emb, amine_emb = self.attention(ald_emb, amine_emb)
        ea, eb, e_pair = self.pooling(ald_emb, amine_emb,
                                       ald_batch, amine_batch, batch_size)

        emb_3d = None
        if self.use_3d and ald_3d is not None and amine_3d is not None:
            emb_3d = self.conformer_branch(ald_3d, amine_3d, dimer_3d)

        return self.film_head(ea, eb, e_pair, emb_3d, rule_vec)

    def _get_features(self, ald_data: Data, amine_data: Data,
                       ald_3d: torch.Tensor | None = None,
                       amine_3d: torch.Tensor | None = None,
                       dimer_3d: torch.Tensor | None = None,
                       ald_batch: torch.Tensor | None = None,
                       amine_batch: torch.Tensor | None = None,
                       batch_size: int = 1) -> tuple[torch.Tensor, ...]:
        """提取 GNN 特征 — 返回 (ea, eb, e_pair, emb_3d)。"""
        ald_emb, amine_emb = self.encoder(ald_data, amine_data)
        ald_emb, amine_emb = self.attention(ald_emb, amine_emb)
        if ald_batch is None:
            ald_batch = torch.zeros(ald_emb.shape[0], dtype=torch.long,
                                    device=ald_emb.device)
        if amine_batch is None:
            amine_batch = torch.zeros(amine_emb.shape[0], dtype=torch.long,
                                      device=amine_emb.device)
        ea, eb, e_pair = self.pooling(ald_emb, amine_emb,
                                       ald_batch, amine_batch, batch_size)
        emb_3d = None
        if self.use_3d and ald_3d is not None and amine_3d is not None:
            if ald_3d.dim() == 1:
                ald_3d = ald_3d.unsqueeze(0)
            if amine_3d.dim() == 1:
                amine_3d = amine_3d.unsqueeze(0)
            if dimer_3d is not None and dimer_3d.dim() == 1:
                dimer_3d = dimer_3d.unsqueeze(0)
            emb_3d = self.conformer_branch(ald_3d, amine_3d, dimer_3d)
        return ea, eb, e_pair, emb_3d

    def _get_features_with_rules(self, ald_data: Data, amine_data: Data,
                                  rule_vec: torch.Tensor | None = None,
                                  ald_3d: torch.Tensor | None = None,
                                  amine_3d: torch.Tensor | None = None,
                                  dimer_3d: torch.Tensor | None = None,
                                  ald_batch: torch.Tensor | None = None,
                                  amine_batch: torch.Tensor | None = None,
                                  batch_size: int = 1) -> tuple[torch.Tensor, ...]:
        """提取 GNN 特征 + 规则向量 — 返回 (ea, eb, e_pair, emb_3d, rule_vec)。"""
        ea, eb, e_pair, emb_3d = self._get_features(
            ald_data, amine_data, ald_3d, amine_3d, dimer_3d,
            ald_batch, amine_batch, batch_size)
        return ea, eb, e_pair, emb_3d, rule_vec

    def encode_single(self, ald_data: Data, amine_data: Data,
                       ald_3d: torch.Tensor | None = None,
                       amine_3d: torch.Tensor | None = None,
                       dimer_3d: torch.Tensor | None = None) -> torch.Tensor:
        """单样本嵌入提取 — 返回 FilmHead 输入向量 [ea, eb, ea⊙eb, e_pair, emb_3d]。
        用于近邻外推检测和 UMAP 可视化。"""
        with torch.no_grad():
            ea, eb, e_pair, emb_3d = self._get_features(
                ald_data, amine_data, ald_3d, amine_3d, dimer_3d)
            parts = [ea, eb, ea * eb, e_pair]
            if emb_3d is not None:
                parts.append(emb_3d)
            return self.film_head.norm(torch.cat(parts, dim=-1))

    def predict_single(self, ald_data: Data, amine_data: Data,
                       ald_3d: torch.Tensor | None = None,
                       amine_3d: torch.Tensor | None = None,
                       dimer_3d: torch.Tensor | None = None,
                       rule_vec: torch.Tensor | None = None) -> torch.Tensor:
        """单样本推理，用于筛选。"""
        with torch.no_grad():
            ea, eb, e_pair, emb_3d = self._get_features(
                ald_data, amine_data, ald_3d, amine_3d, dimer_3d)
            return self.film_head(ea, eb, e_pair, emb_3d, rule_vec)

    def enable_mc_dropout(self) -> None:
        """强制所有 Dropout 层保持激活，用于 MC 不确定性估计。

        调用后 model.eval() 不会关闭 dropout，需手动 model.eval() 恢复。
        """
        for m in self.modules():
            if isinstance(m, nn.Dropout):
                m.train()

    @torch.no_grad()
    def predict_mc(self, ald_data: Data, amine_data: Data,
                   ald_3d: torch.Tensor | None = None,
                   amine_3d: torch.Tensor | None = None,
                   dimer_3d: torch.Tensor | None = None,
                   rule_vec: torch.Tensor | None = None,
                   n_samples: int = 10) -> tuple[torch.Tensor, torch.Tensor]:
        """MC Dropout 推理 — 返回 (均值, 标准差) 用于不确定性估计。
        训练模式下的多次前向传播，dropout 保持激活。"""
        was_training = self.training
        self.train()  # 激活 dropout
        probs = []
        for _ in range(n_samples):
            ea, eb, e_pair, emb_3d = self._get_features(
                ald_data, amine_data, ald_3d, amine_3d, dimer_3d)
            logit = self.film_head(ea, eb, e_pair, emb_3d, rule_vec)
            probs.append(torch.sigmoid(logit))
        if not was_training:
            self.eval()
        stacked = torch.stack(probs)
        return stacked.mean(dim=0), stacked.std(dim=0)
