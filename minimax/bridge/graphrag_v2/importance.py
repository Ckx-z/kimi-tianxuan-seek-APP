"""
graphrag_v2/importance.py
==========================
节点重要性: PageRank + Betweenness Centrality

v2 升级 - 不删减 v1
"""
import json
import pickle
from pathlib import Path

import networkx as nx

GRAPH_DIR = Path(__file__).resolve().parent.parent / 'graphrag'
CACHE_DIR = Path(__file__).resolve().parent / 'cached'
CACHE_DIR.mkdir(exist_ok=True)


def load_graph():
    # 优先加载 graph_v2.pkl (带 importance), fallback graph.pkl
    v2_fp = GRAPH_DIR / 'graph_v2.pkl'
    v1_fp = GRAPH_DIR / 'graph.pkl'
    fp = v2_fp if v2_fp.exists() else v1_fp
    with open(fp, 'rb') as f:
        return pickle.load(f)


def compute_node_importance(G=None, alpha=0.85, save=True):
    """计算 PageRank + Betweenness, 缓存到 G.nodes

    PageRank: 节点被其他重要节点指向的程度 (引用重要性)
    Betweenness: 节点作为桥梁的程度 (关键路径)

    使用纯 Python 实现 (避免 numpy 1.24 / 3.12 冲突)
    """
    if G is None:
        G = load_graph()
    print('Computing PageRank (pure Python)...')
    pr = _pagerank_pure_python(G, alpha=alpha)
    print('Computing Betweenness (pure Python)...')
    bc = _betweenness_pure_python(G)

    # 归一化
    max_pr = max(pr.values()) if pr and max(pr.values()) > 0 else 1
    max_bc = max(bc.values()) if bc and max(bc.values()) > 0 else 1

    # 综合 (使用对数压缩避免极值)
    import math
    log_pr = {n: math.log10(pr[n] * 1e10 + 1) for n in pr}
    log_bc = {n: math.log10(bc[n] * 1e6 + 1) for n in bc}
    max_log_pr = max(log_pr.values()) if log_pr else 1
    max_log_bc = max(log_bc.values()) if log_bc else 1

    importance = {}
    for nid in G.nodes():
        norm_pr = log_pr.get(nid, 0) / max_log_pr
        norm_bc = log_bc.get(nid, 0) / max_log_bc
        score = 0.6 * norm_pr + 0.4 * norm_bc
        importance[nid] = {
            'pagerank': pr.get(nid, 0),
            'betweenness': bc.get(nid, 0),
            'importance': score,
        }
        G.nodes[nid]['pagerank'] = pr.get(nid, 0)
        G.nodes[nid]['betweenness'] = bc.get(nid, 0)
        G.nodes[nid]['importance'] = score

    if save:
        with open(CACHE_DIR / 'importance.json', 'w', encoding='utf-8') as f:
            json.dump(importance, f, ensure_ascii=False, indent=2)
        # 同时保存带 importance 的 graph
        with open(GRAPH_DIR / 'graph_v2.pkl', 'wb') as f:
            pickle.dump(G, f, pickle.HIGHEST_PROTOCOL)
        print(f'✓ Saved: {CACHE_DIR / "importance.json"}')
        print(f'✓ Saved: graph_v2.pkl')

    return importance


def _pagerank_pure_python(G, alpha=0.85, max_iter=100, tol=1e-6):
    """PageRank 纯 Python 实现 (power iteration)
    避免 numpy/scipy 依赖
    """
    # 初始值
    N = G.number_of_nodes()
    if N == 0:
        return {}
    pr = {n: 1.0 / N for n in G.nodes()}

    # 预计算 out-degree
    out_degree = {n: G.out_degree(n) for n in G.nodes()}

    for _ in range(max_iter):
        new_pr = {}
        # dangling sum: 无出边的节点的总 PR
        dangling_sum = sum(pr[n] for n in G.nodes() if out_degree[n] == 0)
        for n in G.nodes():
            # 来自入边的 PR 贡献
            rank = (1 - alpha) / N + alpha * dangling_sum / N
            for _, src in G.in_edges(n):
                if out_degree[src] > 0:
                    rank += alpha * pr[src] / out_degree[src]
            new_pr[n] = rank

        # 检查收敛
        diff = sum(abs(new_pr[n] - pr[n]) for n in G.nodes())
        pr = new_pr
        if diff < tol:
            break

    return pr


def _betweenness_pure_python(G, k=100):
    """Betweenness centrality 纯 Python 实现
    只取前 k 个节点计算 (加速)
    """
    import random
    from collections import deque

    nodes = list(G.nodes())
    if k and k < len(nodes):
        nodes_sample = random.sample(nodes, k)
    else:
        nodes_sample = nodes

    betweenness = {n: 0.0 for n in G.nodes()}

    for s in nodes_sample:
        # BFS from s
        stack = []
        pred = {n: [] for n in G.nodes()}
        sigma = {n: 0 for n in G.nodes()}
        sigma[s] = 1
        dist = {n: -1 for n in G.nodes()}
        dist[s] = 0
        queue = deque([s])

        while queue:
            v = queue.popleft()
            stack.append(v)
            for _, w in G.out_edges(v):
                if dist[w] < 0:
                    dist[w] = dist[v] + 1
                    queue.append(w)
                if dist[w] == dist[v] + 1:
                    sigma[w] += sigma[v]
                    pred[w].append(v)

        delta = {n: 0.0 for n in G.nodes()}
        while stack:
            w = stack.pop()
            for v in pred[w]:
                delta[v] += (sigma[v] / sigma[w]) * (1 + delta[w])
            if w != s:
                betweenness[w] += delta[w]

    # 归一化
    if nodes_sample and len(G.nodes()) > 1:
        scale = 1.0 / (len(nodes_sample) * (len(G.nodes()) - 1) * 0.5)
    else:
        scale = 1.0
    for n in betweenness:
        betweenness[n] *= scale

    return betweenness


def top_k_important(G=None, k=10, node_type=None):
    """返回 top-k 重要节点 (按 type 过滤)"""
    if G is None:
        G = load_graph()

    items = []
    for nid, data in G.nodes(data=True):
        if node_type and data.get('node_type') != node_type:
            continue
        items.append({
            'id': nid,
            'importance': data.get('importance', 0),
            'pagerank': data.get('pagerank', 0),
            'betweenness': data.get('betweenness', 0),
            'data': dict(data),
        })
    items.sort(key=lambda x: -x['importance'])
    return items[:k]


if __name__ == '__main__':
    print('=== Computing Node Importance ===\n')
    importance = compute_node_importance()
    print(f'\nTop 10 most important nodes:')
    G = load_graph()
    top = top_k_important(G, k=10)
    for i, t in enumerate(top, 1):
        d = t['data']
        print(f'  {i}. [{d.get("node_type"):12s}] {t["id"]} (imp={t["importance"]:.4f})')