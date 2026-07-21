"""阶段 13：提取 GNN v5.3 embedding（pair 512 维 + mono 256 维），供树模型迁移评估。

背景（exp_009 / 阶段 13 阶段 A）：
- 双未见单体泛化 0.63-0.68 是真正瓶颈，手工描述符触顶、Morgan 指纹已证伪（exp_006）；
- 本脚本把旧项目 GNN v5.3 的图表示提取为特征文件，供 stage13_gnn_embedding_eval.py
  在双留出协议下评估"GNN embedding 迁移入树模型"的可行性（阶段 A：全量 GNN，乐观上界，
  证伪优先——见 EXPERIMENTS/exp_009.md 的泄漏警告）。

提取点（已实测，不需要 hook、不改旧项目任何文件）：
- pair_emb 512 维：V4Model._get_features → (ea, eb, e_pair)，拼 [ea‖eb‖ea⊙eb‖e_pair]
  （正是 FilmHead 的 GNN 部分输入，配对相关，交叉注意力之后）；
- mono_emb 256 维：encoder 出口（交叉注意力之前）的原子级表示按图 mean-pool，
  醛 128 + 胺 128（配对无关的纯单体表示）。

⚠️ 运行环境：必须用 dphuanjing（torch + PyG + rdkit）：
    E:\\ANACONDA\\envs\\dphuanjing\\python.exe scripts/stage13_extract_gnn_emb.py

输出（data/interim/，不入 git）：
- gnn_emb_v53_pair.npy   float32 [n, 512]
- gnn_emb_v53_mono.npy   float32 [n, 256]
- gnn_emb_v53_index.json 行号 + 醛/胺 SMILES + 校验信息（评估脚本对齐断言用）
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OLD_PROJECT_ROOT = Path(r"C:\Users\ckx\Desktop\tianxuan seek")
CHECKPOINT = OLD_PROJECT_ROOT / "models" / "v5.3" / "v5_model.pt"
DATA_PATH = PROJECT_ROOT / "data" / "interim" / "v5_train_stage1_cond_filled.csv"
OUT_DIR = PROJECT_ROOT / "data" / "interim"

# 旧项目 src 置于 sys.path 最前（只读引用，D07；避免与新项目 src 冲突）
sys.path.insert(0, str(OLD_PROJECT_ROOT))

import torch  # noqa: E402
from rdkit import RDLogger  # noqa: E402
from torch_geometric.data import Batch  # noqa: E402
from torch_geometric.nn import global_mean_pool  # noqa: E402

RDLogger.DisableLog("rdApp.*")  # 静默 RDKit 解析告警洪流（已知脏 SMILES，与 stage11 一致）

from src.screening.gnn_v3.featurizer import smiles_to_graph  # noqa: E402
from src.screening.gnn_v4.model import V4Model  # noqa: E402
from src.chemistry.hard_rules import get_rule_vector, RULE_DIM  # noqa: E402

# 自检锚点：Tp + Pa 的历史 GNN 输出（MC=10 均值 0.665，见 gnn_model.py / 日报 2026-07-20）
SELF_CHECK_ALD = "O=CC1=C(C=O)C(=O)C(C=O)=C1O"
SELF_CHECK_AMINE = "Nc1ccc(N)cc1"
SELF_CHECK_EXPECT = 0.665
SELF_CHECK_TOL = 0.08

BATCH_SIZE = 64


def load_df() -> pd.DataFrame:
    """复刻 stage11_dual_holdout.load_df_y() 的行过滤逻辑（必须逐行对齐）。"""
    df = pd.read_csv(DATA_PATH)
    mask = ~df["source_db"].astype(str).str.startswith("hard_rule")
    df = df[mask].dropna(subset=["aldehyde_smiles", "amine_smiles", "is_film"]).reset_index(drop=True)
    return df


def rebuild_model(device: torch.device) -> V4Model:
    """按 predict_pair.py 的方式重建 v5.3（确定性 eval 模式，不开 MC dropout）。"""
    ckpt = torch.load(CHECKPOINT, map_location=device, weights_only=False)
    cfg = ckpt.get("config", {})
    use_3d = ckpt.get("use_3d", False)
    use_rules = ckpt.get("use_rules", True)
    if use_3d:
        cfg["model"]["use_3d"] = True
    cfg["model"]["use_rules"] = use_rules
    cfg["model"]["dim_rules"] = RULE_DIM
    model = V4Model(cfg).to(device)
    model.load_state_dict(ckpt["model_state"])
    model.eval()
    if use_3d and ckpt.get("scaler_3d"):
        sd = ckpt.get("scaler_dimer")
        model.set_3d_scaler(
            monomer_mean=ckpt["scaler_3d"]["mean"],
            monomer_std=ckpt["scaler_3d"]["std"],
            dimer_mean=sd["mean"] if sd else None,
            dimer_std=sd["std"] if sd else None,
        )
    return model


def self_check(model: V4Model, device: torch.device) -> None:
    """重建完整性自检：Tp+Pa 确定性概率应接近历史 MC 均值 0.665。"""
    ga = smiles_to_graph(SELF_CHECK_ALD, role=0).to(device)
    gb = smiles_to_graph(SELF_CHECK_AMINE, role=1).to(device)
    rv = torch.tensor(get_rule_vector(SELF_CHECK_ALD, SELF_CHECK_AMINE),
                      dtype=torch.float, device=device).unsqueeze(0)
    with torch.no_grad():
        logit = model.predict_single(ga, gb, rule_vec=rv)
        prob = float(torch.sigmoid(logit).item())
    print(f"[self-check] Tp+Pa 确定性概率 = {prob:.4f}（期望 ≈{SELF_CHECK_EXPECT}）", flush=True)
    if abs(prob - SELF_CHECK_EXPECT) > SELF_CHECK_TOL:
        raise RuntimeError(
            f"自检失败：Tp+Pa 概率 {prob:.4f} 与期望 {SELF_CHECK_EXPECT} 偏差超过 "
            f"{SELF_CHECK_TOL}，模型重建有误，终止提取。")


def featurize_all(df: pd.DataFrame):
    """全部 SMILES → 图；失败的行记 None（后续补零向量并计数）。"""
    ald_graphs, amine_graphs, n_fail = [], [], 0
    for i, row in enumerate(df.itertuples()):
        ga = smiles_to_graph(row.aldehyde_smiles, role=0)
        gb = smiles_to_graph(row.amine_smiles, role=1)
        if ga is None or gb is None:
            n_fail += 1
        ald_graphs.append(ga)
        amine_graphs.append(gb)
        if (i + 1) % 500 == 0:
            print(f"[featurize] {i + 1}/{len(df)}", flush=True)
    return ald_graphs, amine_graphs, n_fail


@torch.no_grad()
def extract(model: V4Model, df: pd.DataFrame, ald_graphs, amine_graphs,
            device: torch.device) -> tuple[np.ndarray, np.ndarray]:
    """批量提取 pair_emb [n,512] 与 mono_emb [n,256]；图构建失败的行补零向量。"""
    n = len(df)
    pair_emb = np.zeros((n, 512), dtype=np.float32)
    mono_emb = np.zeros((n, 256), dtype=np.float32)

    for start in range(0, n, BATCH_SIZE):
        idx = [i for i in range(start, min(start + BATCH_SIZE, n))
               if ald_graphs[i] is not None and amine_graphs[i] is not None]
        if not idx:
            continue
        ba = Batch.from_data_list([ald_graphs[i] for i in idx]).to(device)
        bb = Batch.from_data_list([amine_graphs[i] for i in idx]).to(device)
        bs = len(idx)

        # pair embedding（交叉注意力之后）：[ea‖eb‖ea⊙eb‖e_pair]
        ea, eb, e_pair, _ = model._get_features(
            ba, bb, ald_batch=ba.batch, amine_batch=bb.batch, batch_size=bs)
        pair = torch.cat([ea, eb, ea * eb, e_pair], dim=-1)  # [B, 512]
        pair_emb[np.asarray(idx)] = pair.cpu().numpy()

        # mono embedding（交叉注意力之前）：encoder 原子级输出按图 mean-pool
        ha = model.encoder.encoder(ba)  # [Na_total, 128]
        hb = model.encoder.encoder(bb)
        ma = global_mean_pool(ha, ba.batch, size=bs)  # [B, 128]
        mb = global_mean_pool(hb, bb.batch, size=bs)
        mono_emb[np.asarray(idx)] = torch.cat([ma, mb], dim=-1).cpu().numpy()

        if (start // BATCH_SIZE) % 10 == 0:
            print(f"[extract] {start + bs}/{n}", flush=True)

    return pair_emb, mono_emb


def main() -> None:
    t0 = time.time()
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"device = {device}", flush=True)

    df = load_df()
    print(f"样本数（过滤 hard_rule* + dropna 后）: {len(df)}", flush=True)

    model = rebuild_model(device)
    self_check(model, device)

    ald_graphs, amine_graphs, n_fail = featurize_all(df)
    print(f"图构建完成：失败 {n_fail}/{len(df)}（失败行补零向量，与树管线 3D 失败补 0 一致）",
          flush=True)
    # 已知数据有约 5% 脏 SMILES（unclosed ring / 非 SMILES 文本），失败行补零与树管线一致；
    # 阈值 10% 只防灾难性故障（如 featurizer 导入错误导致全灭）
    if n_fail > len(df) * 0.10:
        raise RuntimeError(f"图构建失败率异常过高：{n_fail}/{len(df)}")

    pair_emb, mono_emb = extract(model, df, ald_graphs, amine_graphs, device)

    if np.isnan(pair_emb).any() or np.isnan(mono_emb).any():
        raise RuntimeError("embedding 含 NaN，终止落盘")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pair_path = OUT_DIR / "gnn_emb_v53_pair.npy"
    mono_path = OUT_DIR / "gnn_emb_v53_mono.npy"
    np.save(pair_path, pair_emb)
    np.save(mono_path, mono_emb)

    index = {
        "n_rows": len(df),
        "checkpoint": str(CHECKPOINT),
        "pair_dim": int(pair_emb.shape[1]),
        "mono_dim": int(mono_emb.shape[1]),
        "n_graph_failures_zero_filled": n_fail,
        "aldehyde_smiles": df["aldehyde_smiles"].tolist(),
        "amine_smiles": df["amine_smiles"].tolist(),
        "is_film": df["is_film"].astype(float).tolist(),
        "self_check": {"ald": SELF_CHECK_ALD, "amine": SELF_CHECK_AMINE,
                        "expected": SELF_CHECK_EXPECT, "tol": SELF_CHECK_TOL},
        "extraction_note": "pair_emb=[ea‖eb‖ea⊙eb‖e_pair]（交叉注意力后）；"
                           "mono_emb=encoder 出口 mean-pool（注意力前，醛128+胺128）；"
                           "确定性 eval 模式（无 MC dropout）",
    }
    index_path = OUT_DIR / "gnn_emb_v53_index.json"
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False)

    print(f"[done] pair {pair_emb.shape} -> {pair_path}", flush=True)
    print(f"[done] mono {mono_emb.shape} -> {mono_path}", flush=True)
    print(f"[done] index -> {index_path}，总耗时 {(time.time() - t0) / 60:.1f} 分钟", flush=True)


if __name__ == "__main__":
    main()
