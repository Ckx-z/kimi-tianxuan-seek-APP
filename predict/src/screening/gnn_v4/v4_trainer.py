"""v5 Trainer — Focal Loss + 规则向量，去掉 chem_penalty。"""
from __future__ import annotations

import copy
from typing import Any

import torch
import torch.nn as nn
from sklearn.metrics import average_precision_score
from torch_geometric.data import Data
from torch.utils.data import DataLoader

from src.screening.gnn_v4.v4_loss import FocalLoss


class V4Trainer:
    """v5 训练器 — 纯 Focal Loss (支持连续标签)，规则向量由模型内部处理。"""

    def __init__(self, model: nn.Module, loss_fn: FocalLoss,
                 optimizer: torch.optim.Optimizer,
                 lr_scheduler: Any = None,
                 device: str = "cpu", patience: int = 30,
                 grad_clip: float = 1.0,
                 max_epochs: int = 200,
                 use_amp: bool = False):
        self.model = model
        self.loss_fn = loss_fn
        self.optimizer = optimizer
        self.lr_scheduler = lr_scheduler
        self.device = device
        self.patience = patience
        self.grad_clip = grad_clip
        self.max_epochs = max_epochs
        self.use_amp = use_amp and device.startswith("cuda")
        self.scaler = torch.amp.GradScaler('cuda') if self.use_amp else None

        self.best_state = None
        self.best_pr_auc = 0.0
        self.best_epoch = 0
        self.no_improve = 0
        self.current_epoch = 0

    def _to_device(self, batch: dict) -> dict:
        result = {}
        for k, v in batch.items():
            if isinstance(v, torch.Tensor):
                result[k] = v.to(self.device)
            else:
                result[k] = v
        return result

    def train_epoch(self, loader: DataLoader) -> dict[str, float]:
        self.model.train()
        total_loss = 0.0

        for batch in loader:
            if batch is None:
                continue
            b = self._to_device(batch)
            ald_data = Data(x=b["ald_x"], edge_index=b["ald_edge_index"], edge_attr=b["ald_edge_attr"])
            amine_data = Data(x=b["amine_x"], edge_index=b["amine_edge_index"], edge_attr=b["amine_edge_attr"])

            self.optimizer.zero_grad()
            with torch.amp.autocast('cuda', enabled=self.use_amp):
                logits = self.model(ald_data, amine_data, b["ald_batch"], b["amine_batch"],
                                    b["batch_size"],
                                    ald_3d=b.get("ald_3d"), amine_3d=b.get("amine_3d"),
                                    dimer_3d=b.get("dimer_3d"),
                                    rule_vec=b.get("rule_vec"))
                loss = self.loss_fn(logits, b["film_label"], b.get("quality_weight"))

            if self.use_amp:
                self.scaler.scale(loss).backward()
                self.scaler.unscale_(self.optimizer)
                if self.grad_clip > 0:
                    torch.nn.utils.clip_grad_norm_(self.model.parameters(), self.grad_clip)
                self.scaler.step(self.optimizer)
                self.scaler.update()
            else:
                loss.backward()
                if self.grad_clip > 0:
                    torch.nn.utils.clip_grad_norm_(self.model.parameters(), self.grad_clip)
                self.optimizer.step()

            total_loss += loss.item()

        n = len(loader)
        return {"loss": total_loss / n, "lr": self.optimizer.param_groups[0]["lr"]}

    @torch.no_grad()
    def validate(self, loader: DataLoader) -> dict[str, float]:
        self.model.eval()
        all_probs, all_labels = [], []

        for batch in loader:
            if batch is None:
                continue
            b = self._to_device(batch)
            ald_data = Data(x=b["ald_x"], edge_index=b["ald_edge_index"], edge_attr=b["ald_edge_attr"])
            amine_data = Data(x=b["amine_x"], edge_index=b["amine_edge_index"], edge_attr=b["amine_edge_attr"])
            logits = self.model(ald_data, amine_data, b["ald_batch"], b["amine_batch"],
                                b["batch_size"],
                                ald_3d=b.get("ald_3d"), amine_3d=b.get("amine_3d"),
                                dimer_3d=b.get("dimer_3d"),
                                rule_vec=b.get("rule_vec"))
            probs = torch.sigmoid(logits)
            all_probs.extend(probs.cpu().tolist())
            all_labels.extend(b["film_label"].cpu().tolist())

        bin_labels = [1 if l >= 0.5 else 0 for l in all_labels]
        pr_auc = average_precision_score(bin_labels, all_probs) if len(set(bin_labels)) > 1 else 0.0
        return {"pr_auc": pr_auc}

    def step(self, train_loader: DataLoader, val_loader: DataLoader,
             epoch: int) -> dict[str, float]:
        self.current_epoch = epoch
        train_m = self.train_epoch(train_loader)
        val_m = self.validate(val_loader)

        if self.lr_scheduler is not None:
            self.lr_scheduler.step()

        pr_auc = val_m["pr_auc"]
        if pr_auc > self.best_pr_auc:
            self.best_pr_auc = pr_auc
            self.best_epoch = epoch
            self.best_state = copy.deepcopy(self.model.state_dict())
            self.no_improve = 0
        else:
            self.no_improve += 1

        return {**{f"train_{k}": v for k, v in train_m.items()},
                **{f"val_{k}": v for k, v in val_m.items()},
                "best_pr_auc": self.best_pr_auc, "no_improve": self.no_improve}

    def should_stop(self) -> bool:
        return self.no_improve >= self.patience

    def load_best(self):
        if self.best_state is not None:
            self.model.load_state_dict(self.best_state)
