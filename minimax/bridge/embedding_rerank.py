"""
bridge/embedding_rerank.py
==========================
Phase 4: embedding rerank for GraphRAG

逻辑:
1. 对 literature 节点 text (system + innovation) 算 MiniMax embo-01 embedding (type=db)
2. query 算 embedding (type=query)
3. cosine 重排 top-k 候选

输出:
- bridge/graphrag/lit_embeddings.jsonl (lit_id, vector)
- bridge/graphrag/embedding_meta.json

API: https://api.minimax.chat/v1/embeddings
"""
import os
import json
import time
import pickle
import requests
from pathlib import Path

GRAPH_DIR = Path(__file__).resolve().parent / 'graphrag'
EMBED_MODEL = 'embo-01'
EMBED_DIM = 1536
API_URL = 'https://api.minimax.chat/v1/embeddings'
BATCH_SIZE = 16  # 一次最多 16 个 text


def get_api_key():
    """从环境变量读 API key"""
    key = os.environ.get('MINIMAX_API_KEY') or os.environ.get('MINIMAX_KEY')
    if not key:
        # 尝试 _tmp/set_api_env.ps1
        ps = Path(r'C:\Users\ckx\Desktop\minimax\_tmp\set_api_env.ps1')
        if ps.exists():
            raise RuntimeError('API key 未设置, 请先跑 _tmp/set_api_env.ps1')
        raise RuntimeError('API key 未设置, 请在环境变量 MINIMAX_API_KEY 中提供')
    return key


def embed_batch(texts, type_='db'):
    """调用 MiniMax embedding API

    type_: 'db' (入库) 或 'query' (检索)
    返回: list of vectors (1536 维)
    """
    api_key = get_api_key()
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    }
    payload = {
        'model': EMBED_MODEL,
        'texts': texts,
        'type': type_,
    }
    for attempt in range(3):
        try:
            r = requests.post(API_URL, headers=headers, json=payload, timeout=60)
            r.raise_for_status()
            data = r.json()
            return data.get('vectors', [])
        except Exception as e:
            if attempt < 2:
                time.sleep(2)
                continue
            raise


def cosine(a, b):
    """cosine similarity (纯 Python)"""
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def build_lit_embeddings():
    """给所有 literature 节点算 embedding"""
    print('=== Phase 4: literature embedding ===\n')
    out_fp = GRAPH_DIR / 'lit_embeddings.jsonl'

    # 读所有 literature 节点
    lit_fp = GRAPH_DIR / 'nodes_literature.jsonl'
    with open(lit_fp, encoding='utf-8') as f:
        literals = [json.loads(line) for line in f]
    print(f'文献数: {len(literals)}')

    # 准备 texts
    texts = []
    for lit in literals:
        text = ' '.join([
            str(lit.get('journal', '')),
            str(lit.get('system', '')),
            str(lit.get('innovation', '')),
        ]).strip()
        if not text:
            text = lit.get('literature_id', '')
        # 截断太长
        if len(text) > 2000:
            text = text[:2000]
        texts.append(text)

    # 分批 embed
    embeddings = []
    start = 0
    while start < len(texts):
        batch = texts[start:start + BATCH_SIZE]
        print(f'  embed {start+1} ~ {start+len(batch)} / {len(texts)}')
        vecs = embed_batch(batch, type_='db')
        embeddings.extend(vecs)
        start += BATCH_SIZE
        time.sleep(0.5)  # 限速

    # 写 JSONL
    with open(out_fp, 'w', encoding='utf-8') as f:
        for lit, vec in zip(literals, embeddings):
            row = {'id': lit['id'], 'vector': vec}
            f.write(json.dumps(row) + '\n')
    print(f'\n✓ {len(embeddings)} embeddings -> {out_fp}')

    meta = {
        'build_date': '2026-07-13',
        'model': EMBED_MODEL,
        'dim': EMBED_DIM,
        'count': len(embeddings),
        'source': 'nodes_literature.jsonl',
    }
    with open(GRAPH_DIR / 'embedding_meta.json', 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f'  meta: embedding_meta.json')


def load_lit_embeddings():
    """加载所有 literature embedding"""
    fp = GRAPH_DIR / 'lit_embeddings.jsonl'
    if not fp.exists():
        raise FileNotFoundError(f'先跑 build_lit_embeddings() 生成 {fp}')
    out = {}
    with open(fp, encoding='utf-8') as f:
        for line in f:
            row = json.loads(line)
            out[row['id']] = row['vector']
    return out


def rerank(query_text, candidates, top_k=5):
    """用 embedding rerank 候选 literature

    candidates: list of {'id': ..., 'data': ...}
    返回: rerank 后的 candidates
    """
    if not candidates:
        return candidates

    # 算 query embedding
    q_vec = embed_batch([query_text], type_='query')[0]

    # 算候选 literature embedding
    lit_emb = load_lit_embeddings()

    # cosine
    scored = []
    for c in candidates:
        lid = c['id']
        if lid not in lit_emb:
            scored.append((0, c))
            continue
        sim = cosine(q_vec, lit_emb[lid])
        # 综合: keyword_score + 0.5 * embedding_sim
        combined = c.get('score', 0) + 0.5 * sim
        scored.append((combined, c))

    scored.sort(key=lambda x: -x[0])
    return [c for _, c in scored[:top_k]]


if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == 'build':
        build_lit_embeddings()
    else:
        print('用法:')
        print('  python bridge/embedding_rerank.py build    # 索引所有 literature')
        print('  from embedding_rerank import rerank        # API')