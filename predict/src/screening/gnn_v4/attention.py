"""v4 交叉图注意力 — 双向 Cross-Attention，hidden_dim=128。"""
from __future__ import annotations

import torch
import torch.nn as nn


class CrossGraphAttention(nn.Module):
    """双向多头交叉图注意力。"""

    def __init__(self, hidden_dim: int = 128, num_heads: int = 4,
                 dropout: float = 0.15):
        super().__init__()
        assert hidden_dim % num_heads == 0
        self.hidden_dim = hidden_dim
        self.num_heads = num_heads
        self.head_dim = hidden_dim // num_heads
        self.scale = self.head_dim ** -0.5

        self.q_proj = nn.Linear(hidden_dim, hidden_dim)
        self.k_proj = nn.Linear(hidden_dim, hidden_dim)
        self.v_proj = nn.Linear(hidden_dim, hidden_dim)
        self.out_proj = nn.Linear(hidden_dim, hidden_dim)

        self.dropout = nn.Dropout(dropout)
        self.norm_ald = nn.LayerNorm(hidden_dim)
        self.norm_amine = nn.LayerNorm(hidden_dim)

    def _cross_attend(self, query: torch.Tensor, key: torch.Tensor,
                      value: torch.Tensor) -> torch.Tensor:
        N_q, N_k = query.shape[0], key.shape[0]

        q = self.q_proj(query).view(N_q, self.num_heads, self.head_dim).transpose(0, 1)
        k = self.k_proj(key).view(N_k, self.num_heads, self.head_dim).transpose(0, 1)
        v = self.v_proj(value).view(N_k, self.num_heads, self.head_dim).transpose(0, 1)

        attn = torch.matmul(q, k.transpose(-2, -1)) * self.scale
        attn = torch.softmax(attn, dim=-1)
        attn = self.dropout(attn)

        out = torch.matmul(attn, v)
        out = out.transpose(0, 1).reshape(N_q, self.hidden_dim)
        return self.out_proj(out)

    def forward(self, ald_emb: torch.Tensor, amine_emb: torch.Tensor
                ) -> tuple[torch.Tensor, torch.Tensor]:
        ald_attended = self._cross_attend(ald_emb, amine_emb, amine_emb)
        ald_out = self.norm_ald(ald_emb + self.dropout(ald_attended))

        amine_attended = self._cross_attend(amine_emb, ald_emb, ald_emb)
        amine_out = self.norm_amine(amine_emb + self.dropout(amine_attended))

        return ald_out, amine_out
