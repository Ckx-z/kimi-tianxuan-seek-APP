"""
bridge/build_tianxuan_matrix.py
================================
将 tianxuan 索引的 embedding 部分提取为二进制 .npy-like 格式
加速检索: 5.8 GB JSONL → ~430 MB 纯向量 + 元数据 JSON

输出:
  bridge/tianxuan_vectors.bin  (float32 连续存储)
  bridge/tianxuan_meta.json    (path, text, chunk_id 列表)
"""
import json
import struct
import time
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
SRC = PROJ / 'bridge' / 'knowledge_index_tianxuan.jsonl'
VEC_OUT = PROJ / 'bridge' / 'tianxuan_vectors.bin'
META_OUT = PROJ / 'bridge' / 'tianxuan_meta.json'


def build():
    if not SRC.exists():
        print(f'源文件不存在: {SRC}')
        return

    print(f'读取 {SRC} ...')
    t0 = time.time()

    vectors = []
    meta = []
    dim = 1536

    with open(SRC, encoding='utf-8') as f:
        for i, line in enumerate(f):
            r = json.loads(line)
            vec = r['vector']
            # float32 打包
            vectors.append(struct.pack(f'{len(vec)}f', *vec))
            meta.append({
                'path': r['path'],
                'chunk_id': r['chunk_id'],
                'text': r['text'][:600],
                'source': r.get('source', 'tianxuan-seek'),
            })
            if (i + 1) % 50000 == 0:
                print(f'  {i+1} chunks...')

    print(f'共 {len(meta)} chunks, 写入二进制...')

    # 写向量二进制
    with open(VEC_OUT, 'wb') as f:
        for packed in vectors:
            f.write(packed)

    # 写元数据
    with open(META_OUT, 'w', encoding='utf-8') as f:
        json.dump({'dim': dim, 'count': len(meta), 'chunks': meta}, f, ensure_ascii=False)

    elapsed = time.time() - t0
    vec_mb = VEC_OUT.stat().st_size / 1e6
    meta_mb = META_OUT.stat().st_size / 1e6
    print(f'完成 ({elapsed:.1f}s)')
    print(f'  向量: {VEC_OUT} ({vec_mb:.1f} MB)')
    print(f'  元数据: {META_OUT} ({meta_mb:.1f} MB)')


if __name__ == '__main__':
    build()
