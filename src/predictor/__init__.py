"""预测层：统一预测接口（树模型 + GNN）。

对外提供单一 predict() 函数，内部自动选择可用模型。
GNN 通过 subprocess 调用旧项目 predict_pair.py，避免包名冲突。

树模型两种模式（D22 上线，D23 修订为 routed_strict）：
- 路由模式（默认）：RoutedTreePredictor 按"醛/胺是否在训练池"双模型路由——
  仅醛胺均未见（双未见）→ tree_v4_noTE（外推）；其余（双已见/一新一熟）→ tree_v4（TE 先验）。
- 单模型模式（向后兼容）：显式传 tree_model_path 或 use_routing=False 时，
  加载指定/默认的单个 TreeFilmPredictor，行为与旧版一致。
"""

from __future__ import annotations

from pathlib import Path

from .gnn_model import GNNFilmPredictor
from .routing import RoutedTreePredictor
from .tree_model import MODELS_DIR, TreeFilmPredictor

# 单模型模式的默认树模型（tree_v3：精简规则 + Hadamard 交互 + 单体 3D 描述符）
DEFAULT_TREE_MODEL = MODELS_DIR / "tree_v3.pkl"


class FilmPredictor:
    """统一的 COF 成膜概率预测器。"""

    def __init__(self, use_gnn: bool = True, use_tree: bool = True,
                 tree_model_path: str | Path | None = None,
                 use_routing: bool = True):
        self.use_gnn = use_gnn
        self.use_tree = use_tree

        self.gnn = GNNFilmPredictor() if use_gnn else None

        # 树模型：显式路径 → 单模型模式；否则默认双模型路由（资产缺失时回退单模型）
        self.tree: TreeFilmPredictor | None = None
        self.router: RoutedTreePredictor | None = None
        if use_tree:
            if tree_model_path is not None or not use_routing:
                self.tree = TreeFilmPredictor(
                    model_path=tree_model_path or DEFAULT_TREE_MODEL
                )
            else:
                self.router = RoutedTreePredictor()

        # 树模型是否可用：看能否加载（路由模式要求两个模型 + 单体池都就位）
        self.tree_available = False
        if self.tree:
            try:
                self.tree.load()
                self.tree_available = True
            except Exception:
                self.tree_available = False
        elif self.router:
            try:
                self.router.load()
                self.tree_available = True
            except Exception:
                # 路由资产不全（如 tree_v4_noTE.pkl / monomer_pool.json 缺失）
                # → 回退单模型默认路径，保证 App 可用
                self.router = None
                self.tree = TreeFilmPredictor(model_path=DEFAULT_TREE_MODEL)
                try:
                    self.tree.load()
                    self.tree_available = True
                except Exception:
                    self.tree_available = False

        # GNN 是否可用：通过 subprocess 调用，不在初始化时加载，预测时才知道
        self.gnn_available = self.gnn is not None

    def get_tree_for(self, ald_smiles: str, amine_smiles: str) -> tuple[TreeFilmPredictor | None, dict | None]:
        """返回该输入实际路由到的 (TreeFilmPredictor, 路由信息)。

        单模型模式返回 (self.tree, None)；路由模式返回 (路由到的模型, 路由信息 dict)。
        打分理由（SHAP 归因）据此跟随实际路由的模型。
        """
        if self.router is not None:
            model, key, reason = self.router.route_for(ald_smiles, amine_smiles)
            return model, {"route": key, "route_reason": reason}
        return self.tree, None

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
                if self.router is not None:
                    info = self.router.predict_with_info(ald_smiles, amine_smiles)
                    result["tree_probability"] = info["probability"]
                    result["tree_model_name"] = info["model_name"]
                    result["tree_route"] = info["route"]
                    result["tree_route_reason"] = info["route_reason"]
                else:
                    tree_prob = self.tree.predict_single(ald_smiles, amine_smiles)
                    result["tree_probability"] = tree_prob
                    result["tree_model_name"] = self.tree.model_path.stem
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
