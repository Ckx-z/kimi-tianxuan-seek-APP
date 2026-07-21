"""v4 池化 — 支持 batch 的注意力池化。

关键改进: 使用 batch 向量做 scatter 操作，支持 batch_size > 1。
"""
from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch_geometric.utils import scatter


class BatchedAttentionPool(nn.Module):
    """支持 batch 的多头注意力池化。

    Args:
        hidden_dim: 输入维度
        num_queries: 注意力头数
    """

    def __init__(self, hidden_dim: int = 128, num_queries: int = 4):
        super().__init__()
        self.num_queries = num_queries
        self.hidden_dim = hidden_dim
        self.query = nn.Parameter(torch.empty(num_queries, hidden_dim))
        nn.init.xavier_uniform_(self.query.unsqueeze(0))
        self.query = nn.Parameter(self.query.squeeze(0))
        self.key = nn.Linear(hidden_dim, hidden_dim)
        self.scale = hidden_dim ** -0.5
        self.compress = nn.Sequential(
            nn.Linear(num_queries * hidden_dim, hidden_dim),
            nn.LayerNorm(hidden_dim),
        )

    def forward(self, x: torch.Tensor, batch: torch.Tensor,
                batch_size: int) -> torch.Tensor:
        """
        Args:
            x: [N_total, hidden_dim] 所有样本的原子嵌入拼接
            batch: [N_total] 每个原子所属的样本索引
            batch_size: batch 大小
        Returns:
            [batch_size, hidden_dim]
        """
        k = self.key(x)  # [N_total, hidden_dim]
        results = []

        for b in range(batch_size):
            mask = (batch == b)
            x_b = x[mask]  # [N_b, hidden_dim]
            k_b = k[mask]  # [N_b, hidden_dim]

            scores = torch.einsum('qd,nd->qn', self.query, k_b) * self.scale
            attn = torch.softmax(scores, dim=-1)
            pooled = torch.matmul(attn, x_b)  # [num_queries, hidden_dim]
            pooled = pooled.reshape(-1)  # [num_queries * hidden_dim]
            results.append(self.compress(pooled))

        return torch.stack(results)  # [batch_size, hidden_dim]


class PairPooling(nn.Module):
    """醛胺配对池化 — 支持 batch。

    Args:
        hidden_dim: 输入/输出维度
        num_queries: 单体池化注意力头数
    """

    def __init__(self, hidden_dim: int = 128, num_queries: int = 4):
        super().__init__()
        self.ald_pool = BatchedAttentionPool(hidden_dim, num_queries)
        self.amine_pool = BatchedAttentionPool(hidden_dim, num_queries)

        self.pair_compress = nn.Sequential(
            nn.Linear(hidden_dim * 4, hidden_dim),
            nn.LayerNorm(hidden_dim),
        )

    def forward(self, ald_emb: torch.Tensor, amine_emb: torch.Tensor,
                ald_batch: torch.Tensor, amine_batch: torch.Tensor,
                batch_size: int
                ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """
        Args:
            ald_emb: [N_ald_total, hidden_dim]
            amine_emb: [N_amine_total, hidden_dim]
            ald_batch: [N_ald_total] 原子→样本映射
            amine_batch: [N_amine_total] 原子→样本映射
            batch_size: int
        Returns:
            ea: [batch_size, hidden_dim]
            eb: [batch_size, hidden_dim]
            e_pair: [batch_size, hidden_dim]
        """
        ea = self.ald_pool(ald_emb, ald_batch, batch_size)
        eb = self.amine_pool(amine_emb, amine_batch, batch_size)

        # 原子对交互 — 逐样本处理
        pair_cross_list = []
        for b in range(batch_size):
            ald_mask = (ald_batch == b)
            amine_mask = (amine_batch == b)
            ald_b = ald_emb[ald_mask]  # [N_ald_b, hidden_dim]
            amine_b = amine_emb[amine_mask]  # [N_amine_b, hidden_dim]

            pair_matrix = ald_b @ amine_b.t()
            pair_attn = F.softmax(pair_matrix, dim=-1)
            pair_cross = pair_attn @ amine_b  # [N_ald_b, hidden_dim]
            pair_cross = pair_cross.mean(dim=0)  # [hidden_dim]
            pair_cross_list.append(pair_cross)

        pair_cross = torch.stack(pair_cross_list)  # [batch_size, hidden_dim]

        e_pair = self.pair_compress(
            torch.cat([ea, eb, ea * eb, pair_cross], dim=-1)
        )
        return ea, eb, e_pair
