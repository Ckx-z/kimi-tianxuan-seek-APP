"""
bridge/fast_search.py
=====================
高性能 tianxuan 检索 (二进制向量 + 纯 Python 点积)

比 JSONL 扫描快 ~10x:
  - 二进制向量: struct.unpack → array → fsum
  - 跳过 JSON 解析
  - 支持 top-k 堆排序
"""
import json
import struct
import math
import time
import array
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
VEC_FILE = PROJ / 'bridge' / 'tianxuan_vectors.bin'
META_FILE = PROJ / 'bridge' / 'tianxuan_meta.json'

_DIM = 1536
_STRIDE = _DIM * 4  # float32 = 4 bytes


def _load_meta():
    if not META_FILE.exists():
        return None
    with open(META_FILE, encoding='utf-8') as f:
        return json.load(f)


def _dot(a_bytes, b):
    """二进制向量 (bytes) · query_vec (list) → float"""
    # 用 array 模块快速解析 float32
    a = array.array('f')
    a.frombytes(a_bytes)
    # 点积
    s = 0.0
    for i in range(len(b)):
        s += a[i] * b[i]
    return s


def _norm_bytes(v_bytes):
    a = array.array('f')
    a.frombytes(v_bytes)
    return math.sqrt(sum(x * x for x in a))


def search_tianxuan(query_vec, top_k=10, min_sim=0.5):
    """
    检索 tianxuan 二进制索引

    query_vec: list of float (1536 维, type=query embedding)
    return: list of (sim, meta_dict)
    """
    meta = _load_meta()
    if meta is None:
        print('[fast_search] 二进制索引不存在, 先运行 build_tianxuan_matrix.py')
        return []

    q_norm = math.sqrt(sum(x * x for x in query_vec)) + 1e-10

    # 预计算所有向量的 norm (首次运行时缓存)
    norm_cache_file = PROJ / 'bridge' / 'tianxuan_norms.bin'
    if norm_cache_file.exists():
        with open(norm_cache_file, 'rb') as f:
            norms = array.array('f')
            norms.frombytes(f.read())
    else:
        # 计算并缓存
        norms = array.array('f')
        with open(VEC_FILE, 'rb') as f:
            while True:
                chunk = f.read(_STRIDE)
                if not chunk or len(chunk) < _STRIDE:
                    break
                a = array.array('f')
                a.frombytes(chunk)
                norms.append(math.sqrt(sum(x * x for x in a)))
        with open(norm_cache_file, 'wb') as f:
            f.write(norms.tobytes())

    # 线性扫描 + top-k
    results = []
    with open(VEC_FILE, 'rb') as f:
        for idx in range(meta['count']):
            vec_bytes = f.read(_STRIDE)
            if len(vec_bytes) < _STRIDE:
                break
            # 点积
            dot_val = _dot(vec_bytes, query_vec)
            sim = dot_val / (q_norm * norms[idx])
            if sim >= min_sim:
                results.append((sim, meta['chunks'][idx]))

    results.sort(key=lambda x: x[0], reverse=True)
    return results[:top_k]


def build_if_needed():
    """如果二进制索引不存在, 自动构建"""
    if not VEC_FILE.exists() or not META_FILE.exists():
        print('[fast_search] 构建二进制索引...')
        from build_tianxuan_matrix import build
        build()


if __name__ == '__main__':
    # 测试
    from search_local_pdfs import _compute_query_embedding

    q = 'imine COF membrane synthesis'
    print(f'Query: {q}')
    q_vec = _compute_query_embedding(q)
    if q_vec:
        build_if_needed()
        t0 = time.time()
        r = search_tianxuan(q_vec, top_k=5, min_sim=0.5)
        elapsed = time.time() - t0
        print(f'Results: {len(r)} hits in {elapsed:.1f}s')
        for sim, m in r:
            print(f'  [{sim:.3f}] {m["path"][-60:]}')
