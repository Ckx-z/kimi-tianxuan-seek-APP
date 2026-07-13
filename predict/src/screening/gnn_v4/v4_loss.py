"""v4 损失函数 — 纯 Focal Loss，无多任务。"""
from __future__ import annotations

import torch
import torch.nn as nn


class FocalLoss(nn.Module):
    """Focal Loss for soft-label classification.

    支持连续标签 (0.0, 0.7, 0.8, 1.0)。
    p_t = 1 - |target - prob|，连续标签下平滑过渡。
    """

    def __init__(self, alpha: float = 0.75, gamma: float = 2.0):
        super().__init__()
        self.alpha = alpha
        self.gamma = gamma

    def forward(self, logits: torch.Tensor, targets: torch.Tensor,
                weights: torch.Tensor | None = None) -> torch.Tensor:
        probs = torch.sigmoid(logits)
        p_t = 1.0 - torch.abs(targets - probs)
        p_t = torch.clamp(p_t, min=1e-7, max=1.0 - 1e-7)
        alpha_t = self.alpha * targets + (1 - self.alpha) * (1 - targets)
        focal_weight = alpha_t * (1 - p_t).pow(self.gamma)

        bce = nn.functional.binary_cross_entropy_with_logits(
            logits, targets, reduction='none'
        )
        loss = focal_weight * bce

        if weights is not None:
            loss = loss * weights

        return loss.mean()
