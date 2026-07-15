"""预测层：统一预测接口（树模型 + GNN）。

对外提供单一 predict() 函数，内部自动选择可用模型。
GNN 通过 subprocess 调用旧项目 predict_pair.py，避免包名冲突。
"""

from __future__ import annotations

from .gnn_model import GNNFilmPredictor
from .tree_model import TreeFilmPredictor


class FilmPredictor:
    """统一的 COF 成膜概率预测器。"""

    def __init__(self, use_gnn: bool = True, use_tree: bool = True):
        self.use_gnn = use_gnn
        self.use_tree = use_tree

        self.gnn = GNNFilmPredictor() if use_gnn else None
        self.tree = TreeFilmPredictor() if use_tree else None

        # 树模型是否可用：看能否加载
        self.tree_available = False
        if self.tree:
            try:
                self.tree.load()
                self.tree_available = True
            except Exception:
                self.tree_available = False

        # GNN 是否可用：通过 subprocess 调用，不在初始化时加载，预测时才知道
        self.gnn_available = self.gnn is not None

    def predict(self, ald_smiles: str, amine_smiles: str) -> dict:
        """预测成膜概率，返回包含各模型结果的字典。"""
        result = {"ald_smiles": ald_smiles, "amine_smiles": amine_smiles}

        if self.gnn_available:
            try:
                gnn_res = self.gnn.predict_single(ald_smiles, amine_smiles)
                result["gnn_probability"] = gnn_res["probability"]
                result["gnn_std"] = gnn_res["std"]
            except Exception as e:
                result["gnn_error"] = str(e)
                self.gnn_available = False

        if self.tree_available:
            try:
                tree_prob = self.tree.predict_single(ald_smiles, amine_smiles)
                result["tree_probability"] = tree_prob
            except Exception as e:
                result["tree_error"] = str(e)
                self.tree_available = False

        # 综合概率：如果有两个模型，取平均；否则取可用的
        probs = []
        if "gnn_probability" in result:
            probs.append(result["gnn_probability"])
        if "tree_probability" in result:
            probs.append(result["tree_probability"])

        if probs:
            result["ensemble_probability"] = sum(probs) / len(probs)
        else:
            result["ensemble_probability"] = None
            result["error"] = "没有可用的预测模型。"

        return result
