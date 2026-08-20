# -*- coding: utf-8 -*-
"""
bridge/user_graph.py
====================
用户实验记录增量侧车图（对标评估 P1：实验记录入图）

设计约束（来自分发模式）：
  - graph.pkl / graph_v2.pkl 随安装包分发，frozen 时在 _MEIPASS 内【只读】
  - 用户自己的实验记录（data/rag_export/records/）增量写入【用户数据目录】
    下的侧车图 graph_user.pkl，运行时合并进内存图，绝不改包内图
  - 节点 ID 方案与 build_graphrag.py 完全一致（md5 哈希），保证用户增量
    能与包内图自然对齐合并（同一 SMILES 的单体会连到包内已有 M- 节点）

节点/边方案：
  - 实验记录 → EXP-<record_id> 节点，node_type='reaction'（复用 v1 检索/
    路由/多跳全套链路），source='user_experiment' 标记出处，
    附带 record_id / outcome / failure_class / mistakes / self_summary 等
  - 边沿用包内图 edge_type：reaction_uses_aldehyde / reaction_uses_amine /
    reaction_uses_solvent / reaction_uses_catalyst / reaction_produces
  - outcome 节点：O-film 复用包内已有节点；O-partial / O-failed 为用户侧新增

用法：
  from user_graph import append_records, merge_into, user_graph_path
  n = append_records(records, app_root=app_root)   # 增量写入侧车图
  G = pickle.load(open('graph_v2.pkl', 'rb'))
  merge_into(G, app_root=app_root)                 # 运行时合并
"""
import datetime
import hashlib
import json
import os
import pickle
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent           # minimax/bridge
PROJECT_ROOT = HERE.parent.parent                # 项目根（源码运行时）

SIDECAR_REL = Path('data') / 'graphrag_user' / 'graph_user.pkl'
META_REL = Path('data') / 'graphrag_user' / 'meta.json'

# 用户实验记录的 outcome → 图 outcome 节点
_OUTCOME_NODE = {'film': 'O-film', 'partial': 'O-partial', 'failed': 'O-failed'}

# 溶剂/催化剂多组分分隔符
_SEP_CHARS = '/、;；,，'


# ---------------------------------------------------------------- 路径解析

def default_app_root() -> Path:
    """用户数据应用根（与 src/runtime_config.user_app_root 同口径，但不依赖 src）：
    COF_DATA_DIR 环境变量 > frozen 时 %APPDATA%/COF-Film-Recommend > 项目根（源码运行）
    """
    env = os.environ.get('COF_DATA_DIR', '').strip()
    if env:
        return Path(env)
    if getattr(sys, 'frozen', False):
        base = os.environ.get('APPDATA') or str(Path.home())
        return Path(base) / 'COF-Film-Recommend'
    return PROJECT_ROOT


def user_graph_path(app_root=None) -> Path:
    """侧车图路径：app_root 缺省时按 default_app_root() 解析"""
    root = Path(app_root) if app_root else default_app_root()
    return root / SIDECAR_REL


def user_meta_path(app_root=None) -> Path:
    root = Path(app_root) if app_root else default_app_root()
    return root / META_REL


# ---------------------------------------------------------------- ID 方案（与 build_graphrag.py 一致）

def _md5_id(prefix: str, text: str) -> str:
    return prefix + '-' + hashlib.md5(text.encode()).hexdigest()[:12]


def monomer_id(m: dict):
    """单体对象 → 图节点 ID。有 SMILES 时与包内图同一方案（可对齐合并）"""
    m = m or {}
    smi = str(m.get('smiles') or '').strip()
    if smi:
        return _md5_id('M', smi)
    name = str(m.get('name') or '').strip()
    cas = str(m.get('cas') or '').strip()
    if name or cas:
        return _md5_id('M', f'namecas:{name}|{cas}')
    return None


def _split_components(text: str):
    """多组分文本 → 组分列表（溶剂/催化剂）"""
    text = str(text or '').strip()
    if not text:
        return []
    parts = [text]
    for ch in _SEP_CHARS:
        parts = [p2 for p in parts for p2 in p.split(ch)]
    return [p.strip() for p in parts if p.strip()]


# ---------------------------------------------------------------- 记录 → 图元素

def _timeline_digest(rec: dict) -> str:
    segs = []
    for e in rec.get('timeline') or []:
        if not isinstance(e, dict):
            continue
        label = str(e.get('time_label') or '').strip()
        desc = str(e.get('description') or '').strip()
        if label or desc:
            segs.append(f'{label}: {desc}' if label else desc)
    return ' → '.join(segs)


def record_node_id(rec: dict):
    rid = str(rec.get('record_id') or '').strip()
    return f'EXP-{rid}' if rid else None


def record_to_graph_items(rec: dict):
    """一条 experiment_record → (节点 dict {id: attrs}, 边列表 [(u, v, edge_type)])

    outcome/时间线/自我总结/失误全部入节点属性，使检索与多跳可命中
    用户自己的实验历史。
    """
    exp_id = record_node_id(rec)
    if exp_id is None:
        return {}, []

    ald = rec.get('aldehyde') or {}
    ami = rec.get('amine') or {}
    cond = rec.get('conditions') or {}
    outcome = str(rec.get('outcome') or '').strip()
    solvent_text = str(cond.get('solvent_1') or cond.get('solvent') or '').strip()
    catalyst_text = str(cond.get('catalyst') or '').strip()
    temp = cond.get('temperature_c', '')

    nodes = {}
    edges = []

    nodes[exp_id] = {
        'node_type': 'reaction',
        'source': 'user_experiment',
        'record_id': rec.get('record_id'),
        'experiment_no': rec.get('experiment_no', ''),
        'favorite_id': rec.get('favorite_id') or '',
        'date': rec.get('date', ''),
        'aldehyde_name': str(ald.get('name') or ald.get('cas') or ''),
        'amine_name': str(ami.get('name') or ami.get('cas') or ''),
        'solvent': solvent_text,
        'catalyst': catalyst_text,
        'temperature': str(temp),
        'outcome': outcome,
        'synthesis_mode': '',
        'interface_type': '',
        'failure_class': rec.get('failure_class') or '',
        'strength': str(rec.get('strength') or ''),
        'notes': str(rec.get('notes') or ''),
        'mistakes': str(rec.get('mistakes') or ''),
        'self_summary': str(rec.get('self_summary') or ''),
        'process_notes': str(rec.get('process_notes') or '')[:500],
        'timeline': _timeline_digest(rec)[:500],
    }

    for m, etype in ((ald, 'reaction_uses_aldehyde'), (ami, 'reaction_uses_amine')):
        mid = monomer_id(m)
        if mid:
            nodes.setdefault(mid, {
                'node_type': 'monomer',
                'source': 'user_experiment',
                'best_name': str(m.get('name') or ''),
                'cas': str(m.get('cas') or ''),
                'smiles': str(m.get('smiles') or ''),
            })
            edges.append((exp_id, mid, etype))

    for s in _split_components(solvent_text):
        sid = _md5_id('S', s.lower())
        nodes.setdefault(sid, {'node_type': 'solvent', 'name': s,
                               'source': 'user_experiment'})
        edges.append((exp_id, sid, 'reaction_uses_solvent'))

    for c in _split_components(catalyst_text):
        cid = _md5_id('C', c.lower())
        nodes.setdefault(cid, {'node_type': 'catalyst', 'name': c,
                               'source': 'user_experiment'})
        edges.append((exp_id, cid, 'reaction_uses_catalyst'))

    oid = _OUTCOME_NODE.get(outcome)
    if oid:
        nodes.setdefault(oid, {'node_type': 'outcome', 'name': outcome,
                               'source': 'user_experiment'})
        edges.append((exp_id, oid, 'reaction_produces'))

    return nodes, edges


# ---------------------------------------------------------------- 增量写入 / 合并加载

def load_user_graph(app_root=None):
    """加载侧车图；不存在/损坏返回 None（调用方按无增量处理）"""
    fp = user_graph_path(app_root)
    if not fp.exists():
        return None
    try:
        with open(fp, 'rb') as f:
            return pickle.load(f)
    except Exception:
        return None


def append_records(records, app_root=None):
    """把实验记录增量写入侧车图（幂等：同 record_id 覆盖更新，不产生重复边）

    返回 (写入的记录数, 侧车图路径)；records 为空或无有效记录时返回 (0, 路径)
    且不写文件。
    """
    import networkx as nx

    fp = user_graph_path(app_root)
    G = load_user_graph(app_root)
    if G is None:
        G = nx.MultiDiGraph()

    n = 0
    for rec in records or []:
        exp_id = record_node_id(rec)
        if exp_id is None:
            continue
        nodes, edges = record_to_graph_items(rec)
        if exp_id in G:
            G.remove_node(exp_id)  # 覆盖更新：删旧节点（连带旧边）再重建
        for nid, attrs in nodes.items():
            if nid in G and nid != exp_id:
                continue  # 不动已有节点（含包内图对齐过来的单体/溶剂节点）
            G.add_node(nid, **attrs)
        for u, v, etype in edges:
            if not G.has_edge(u, v, key=etype):
                G.add_edge(u, v, key=etype, edge_type=etype)
        n += 1

    if n == 0:
        return 0, fp

    fp.parent.mkdir(parents=True, exist_ok=True)
    with open(fp, 'wb') as f:
        pickle.dump(G, f, pickle.HIGHEST_PROTOCOL)
    meta = {
        'updated_at': datetime.datetime.now().isoformat(timespec='seconds'),
        'n_nodes': G.number_of_nodes(),
        'n_edges': G.number_of_edges(),
        'n_experiment_nodes': sum(
            1 for _, d in G.nodes(data=True)
            if d.get('source') == 'user_experiment'
            and d.get('node_type') == 'reaction'),
    }
    user_meta_path(app_root).write_text(
        json.dumps(meta, ensure_ascii=False, indent=1), encoding='utf-8')
    return n, fp


def merge_into(G, app_root=None):
    """把侧车图合并进内存图 G（运行时调用）。

    - 包内已有节点（同 ID 的单体/溶剂等）保留包内属性，只补边
    - 返回合并进来的实验记录节点数；无侧车图/加载失败返回 0
    """
    UG = load_user_graph(app_root)
    if UG is None:
        return 0
    n_exp = 0
    for nid, attrs in UG.nodes(data=True):
        if nid not in G:
            G.add_node(nid, **attrs)
        if attrs.get('source') == 'user_experiment' \
                and attrs.get('node_type') == 'reaction':
            n_exp += 1
    for u, v, _, edata in UG.edges(data=True, keys=True):
        etype = edata.get('edge_type', '')
        # 包内图边 key 是自增整数，不能按 key 判重；按 (u, v, edge_type) 判重
        existing = {d.get('edge_type')
                    for d in G.get_edge_data(u, v, default={}).values()}
        if etype not in existing:
            G.add_edge(u, v, **edata)
    return n_exp


if __name__ == '__main__':
    # 体检：打印侧车图状态
    fp = user_graph_path()
    print(f'侧车图路径: {fp}')
    G = load_user_graph()
    if G is None:
        print('(侧车图不存在：尚未摄入任何实验记录)')
    else:
        print(f'节点 {G.number_of_nodes()} / 边 {G.number_of_edges()}')
        for nid, d in G.nodes(data=True):
            if d.get('node_type') == 'reaction':
                print(f'  {nid}: outcome={d.get("outcome")} '
                      f'failure_class={d.get("failure_class")} '
                      f'醛={d.get("aldehyde_name", "")[:30]} '
                      f'胺={d.get("amine_name", "")[:30]}')
