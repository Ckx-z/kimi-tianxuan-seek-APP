"""
graphrag_v2/multimodal.py
=========================
多模态融合打分 (v2 升级)

4 路打分:
- keyword (文本匹配)
- embedding (MiniMax cosine)
- importance (PageRank + betweenness)
- community (社区归属)

加权融合排序
"""
import sys
from pathlib import Path

# 让 embedding_rerank 可被调用
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    from embedding_rerank import load_lit_embeddings, cosine
    HAS_EMBED = True
except ImportError:
    HAS_EMBED = False

sys.path.insert(0, str(Path(__file__).resolve().parent))


def normalize_dict(d):
    """归一化字典值到 [0, 1]"""
    if not d:
        return {}
    vals = list(d.values())
    if not vals:
        return {}
    min_v = min(vals)
    max_v = max(vals)
    if max_v == min_v:
        return {k: 0.5 for k in d}
    return {k: (v - min_v) / (max_v - min_v) for k, v in d.items()}


def multimodal_rerank(candidates, query_text, G=None, weights=None):
    """多模态融合重排

    candidates: list of {'id': ..., 'data': ..., 'keyword_score': N}
    query_text: str
    G: NetworkX 图 (含 importance)

    weights: dict {'keyword': 0.25, 'embedding': 0.30, 'importance': 0.20, 'community': 0.25}
    """
    if not candidates:
        return []

    if weights is None:
        weights = {
            'keyword': 0.30,
            'embedding': 0.30,
            'importance': 0.20,
            'community': 0.20,
        }

    # 1. keyword scores
    kw_scores = {c['id']: c.get('score', c.get('keyword_score', 0)) for c in candidates}

    # 2. embedding scores (only for literature with embeddings)
    emb_scores = {c['id']: 0 for c in candidates}
    if HAS_EMBED:
        try:
            from embedding_rerank import embed_batch
            q_vec = embed_batch([query_text], type_='query')[0]
            lit_emb = load_lit_embeddings()
            for c in candidates:
                lid = c['id']
                if lid in lit_emb:
                    emb_scores[lid] = cosine(q_vec, lit_emb[lid])
        except Exception as e:
            print(f'  (embedding rerank skipped: {e})')

    # 3. importance scores
    imp_scores = {c['id']: 0 for c in candidates}
    if G is not None:
        for c in candidates:
            nid = c['id']
            imp_scores[nid] = G.nodes[nid].get('importance', 0)

    # 4. community scores (有 community summary 节点的加分)
    comm_scores = {c['id']: 0 for c in candidates}
    if G is not None:
        for c in candidates:
            nid = c['id']
            # 看是否有 belongs_to 边连到 community
            for _, dst, edata in G.out_edges(nid, data=True):
                if edata.get('edge_type') == 'belongs_to':
                    comm_scores[nid] = 1.0
                    break

    # 归一化
    kw_norm = normalize_dict(kw_scores)
    emb_norm = normalize_dict(emb_scores)
    imp_norm = normalize_dict(imp_scores)
    comm_norm = normalize_dict(comm_scores)

    # 加权融合
    final_scores = {}
    for cid in kw_scores:
        final_scores[cid] = (
            weights['keyword'] * kw_norm.get(cid, 0) +
            weights['embedding'] * emb_norm.get(cid, 0) +
            weights['importance'] * imp_norm.get(cid, 0) +
            weights['community'] * comm_norm.get(cid, 0)
        )

    # 排序
    sorted_candidates = sorted(candidates, key=lambda c: -final_scores.get(c['id'], 0))

    # 加 score
    for c in sorted_candidates:
        c['multimodal_score'] = final_scores.get(c['id'], 0)
        c['score_breakdown'] = {
            'keyword': kw_norm.get(c['id'], 0),
            'embedding': emb_norm.get(c['id'], 0),
            'importance': imp_norm.get(c['id'], 0),
            'community': comm_norm.get(c['id'], 0),
        }

    return sorted_candidates


if __name__ == '__main__':
    # 测试
    print('=== Multimodal Rerank Test ===\n')
    candidates = [
        {'id': 'L-a', 'data': {'journal': 'JACS', 'system': 'test'}, 'score': 2},
        {'id': 'L-b', 'data': {'journal': 'Nature', 'system': 'test2'}, 'score': 1},
    ]
    result = multimodal_rerank(candidates, 'test query')
    for r in result:
        print(f'  {r["id"]}: multimodal={r["multimodal_score"]:.3f} breakdown={r["score_breakdown"]}')