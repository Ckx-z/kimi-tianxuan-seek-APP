"""v4 GNN 编码器 — GIN+GINE x3 + JK-Net (mean) + 残差 + LayerNorm。

缩小版: hidden_dim=128, layers=3, 参数量 ~0.3M。
与 v3 编码器结构相同，仅超参数不同。
"""
from __future__ import annotations

import torch
import torch.nn as nn
from torch_geometric.nn import GINEConv
from torch_geometric.data import Data


class GINELayer(nn.Module):
    """单层 GINE: 边特征经 MLP 映射后加法式融入消息传递, eps=0。"""

    def __init__(self, hidden_dim: int = 128, dropout: float = 0.15):
        super().__init__()
        self.edge_mlp = nn.Sequential(
            nn.Linear(5, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, hidden_dim),
        )
        self.node_mlp = nn.Sequential(
            nn.Linear(hidden_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, hidden_dim),
        )
        self.conv = GINEConv(self.node_mlp, edge_dim=hidden_dim, eps=0.0)
        self.norm = nn.LayerNorm(hidden_dim)
        self.dropout = nn.Dropout(dropout)

    def forward(self, x: torch.Tensor, edge_index: torch.Tensor,
                edge_attr: torch.Tensor) -> torch.Tensor:
        edge_emb = self.edge_mlp(edge_attr)
        h = self.conv(x, edge_index, edge_emb)
        h = self.norm(h)
        h = self.dropout(h)
        return h


class GINEncoder(nn.Module):
    """GIN+GINE + JK-Net (mean) + 残差 + LayerNorm。"""

    def __init__(self, in_dim: int = 37, hidden_dim: int = 128,
                 num_layers: int = 3, dropout: float = 0.15):
        super().__init__()
        self.num_layers = num_layers
        self.hidden_dim = hidden_dim

        self.input_proj = nn.Linear(in_dim, hidden_dim)
        self.layers = nn.ModuleList([
            GINELayer(hidden_dim, dropout) for _ in range(num_layers)
        ])
        self.output_norm = nn.LayerNorm(hidden_dim)

    def forward(self, data: Data) -> torch.Tensor:
        x, edge_index, edge_attr = data.x, data.edge_index, data.edge_attr
        h = self.input_proj(x)
        hidden_states = []

        for layer in self.layers:
            h_new = layer(h, edge_index, edge_attr)
            h = h + h_new
            hidden_states.append(h)

        stacked = torch.stack(hidden_states, dim=0)
        pooled = stacked.mean(dim=0)
        return self.output_norm(pooled)


class SiameseEncoder(nn.Module):
    """Siamese 共享编码器 — 醛和胺用同一套 GIN 权重。"""

    def __init__(self, in_dim: int = 37, hidden_dim: int = 128,
                 num_layers: int = 3, dropout: float = 0.15):
        super().__init__()
        self.encoder = GINEncoder(in_dim, hidden_dim, num_layers, dropout)

    def forward(self, ald_data: Data, amine_data: Data
                ) -> tuple[torch.Tensor, torch.Tensor]:
        ald_emb = self.encoder(ald_data)
        amine_emb = self.encoder(amine_data)
        return ald_emb, amine_emb
