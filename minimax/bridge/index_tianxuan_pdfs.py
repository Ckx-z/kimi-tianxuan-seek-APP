"""
bridge/index_tianxuan_pdfs.py
=============================
索引 tianxuan-seek/data/pdf/ 全部 PDF 到本地知识库索引

特性:
  - 增量处理 (file hash, 跳过已索引)
  - 断点续传 (每 50 个 PDF 写 checkpoint)
  - MiniMax embo-01 embedding (batch_size=8)
  - 搜索时与其它索引合并

用法:
  python bridge/index_tianxuan_pdfs.py          # 开始/继续索引
  python bridge/index_tianxuan_pdfs.py --limit 100   # 限制处理 N 个 (测试)
"""
import os
import sys
import json
import time
import hashlib
import argparse
import requests
from pathlib import Path

# ---- 路径 ----
PROJ = Path(__file__).resolve().parent.parent
TIANXUAN_DIR = Path(r'C:\Users\ckx\Desktop\tianxuan seek\data\pdfs')
INDEX_OUT = PROJ / 'bridge' / 'knowledge_index_tianxuan.jsonl'
META_OUT = PROJ / 'bridge' / 'knowledge_meta_tianxuan.json'
PROGRESS_FILE = PROJ / 'bridge' / '.tianxuan_index_progress.json'

# ---- MiniMax ----
EMBEDDING_API = 'https://api.minimax.chat/v1/embeddings'
EMBEDDING_MODEL = 'embo-01'
EMBEDDING_DIM = 1536
CHUNK_SIZE = 500
CHUNK_OVERLAP = 100
BATCH_SIZE = 8


def get_api_key():
    key = os.environ.get('MINIMAX_API_KEY')
    if not key:
        raise RuntimeError('MINIMAX_API_KEY 未设置')
    return key


def compute_embedding(texts, embed_type='db'):
    api_key = get_api_key()
    r = requests.post(
        EMBEDDING_API,
        headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
        json={'model': EMBEDDING_MODEL, 'texts': texts, 'type': embed_type},
        timeout=60,
    )
    r.raise_for_status()
    j = r.json()
    if j.get('base_resp', {}).get('status_code', 0) != 0:
        raise RuntimeError(f'Embedding API error: {j.get("base_resp", {})}')
    return j['vectors']


def file_hash(path, size=8192):
    """前 8KB 的 MD5 作为文件标识"""
    h = hashlib.md5()
    with open(path, 'rb') as f:
        h.update(f.read(size))
    return h.hexdigest()


def parse_pdf(path):
    import pdfplumber
    text_parts = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            t = page.extract_text() or ''
            text_parts.append(t)
    return '\n\n'.join(text_parts)


def chunk_text(text, chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    import re
    text = text.strip()
    if not text:
        return []
    paragraphs = re.split(r'\n\s*\n', text)
    chunks = []
    current = ''
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if len(para) > chunk_size:
            if current:
                chunks.append(current)
                current = ''
            for i in range(0, len(para), chunk_size - overlap):
                chunks.append(para[i:i + chunk_size])
            continue
        if len(current) + len(para) + 2 > chunk_size:
            chunks.append(current)
            current = para
        else:
            current = (current + '\n\n' + para) if current else para
    if current:
        chunks.append(current)
    return chunks


def load_progress():
    """加载进度 (已处理的文件 hash 集合)"""
    if PROGRESS_FILE.exists():
        return json.loads(PROGRESS_FILE.read_text(encoding='utf-8'))
    return {'hashes': [], 'total_chunks': 0, 'total_files': 0}


def recover_from_index():
    """从已存在的索引中恢复进度 (防重启后重复)"""
    if not INDEX_OUT.exists():
        return set()
    print('扫描已有索引, 恢复进度...')
    paths = set()
    with open(INDEX_OUT, encoding='utf-8') as f:
        for line in f:
            try:
                r = json.loads(line)
                paths.add(r['path'])
            except:
                continue
    print(f'  已有索引包含 {len(paths)} 个文件')
    return paths


def save_progress(progress):
    PROGRESS_FILE.write_text(json.dumps(progress, ensure_ascii=False), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, help='限制处理 PDF 数量 (测试)')
    args = parser.parse_args()

    pdfs = sorted(TIANXUAN_DIR.rglob('*.pdf'))
    print(f'发现 PDF: {len(pdfs)} 个')

    # 加载进度
    progress = load_progress()
    done_hashes = set(progress['hashes'])
    print(f'已完成 (hash): {len(done_hashes)} 个')

    # 从已有索引恢复 (防重复)
    indexed_paths = recover_from_index()
    print(f'已完成 (索引): {len(indexed_paths)} 个')

    # 把已索引路径的 hash 也加入 done_hashes (防后续重复)
    for p_str in indexed_paths:
        try:
            done_hashes.add(file_hash(Path(p_str)))
        except:
            pass
    save_progress(progress)

    # 过滤
    remaining = [p for p in pdfs if file_hash(p) not in done_hashes and str(p) not in indexed_paths]
    print(f'待处理: {len(remaining)} 个')

    if args.limit:
        remaining = remaining[:args.limit]
        print(f'限制处理前 {args.limit} 个')

    if not remaining:
        print('没有需要处理的 PDF')
        return

    total_new_chunks = 0
    start_time = time.time()

    for i, path in enumerate(remaining):
        i += 1
        t0 = time.time()
        print(f'[{i}/{len(remaining)}] {path.name} ', end='', flush=True)

        try:
            text = parse_pdf(path)
        except Exception as e:
            print(f'解析失败: {e}')
            progress['hashes'].append(file_hash(path))
            save_progress(progress)
            continue

        if not text.strip():
            print('空文本, 跳过')
            progress['hashes'].append(file_hash(path))
            save_progress(progress)
            continue

        chunks = chunk_text(text)
        print(f'{len(chunks)}c ', end='', flush=True)

        # 分批 embedding
        success_count = 0
        for bi in range(0, len(chunks), BATCH_SIZE):
            batch = chunks[bi:bi + BATCH_SIZE]
            try:
                vectors = compute_embedding(batch, embed_type='db')
                for j, vec in enumerate(vectors):
                    chunk_idx = bi + j
                    record = {
                        'path': str(path),
                        'chunk_id': f'{path.stem}_{chunk_idx:03d}',
                        'text': batch[j].replace('\n', ' ').strip()[:600],
                        'vector': vec,
                        'dim': len(vec),
                        'source': 'tianxuan-seek',
                    }
                    with open(INDEX_OUT, 'a', encoding='utf-8') as f:
                        f.write(json.dumps(record, ensure_ascii=False) + '\n')
                    success_count += 1
            except Exception as e:
                print(f'\n  embed 失败: {e}')
                continue

        total_new_chunks += success_count
        progress['hashes'].append(file_hash(path))
        progress['total_chunks'] = progress.get('total_chunks', 0) + success_count
        progress['total_files'] = progress.get('total_files', 0) + 1
        save_progress(progress)

        dt = time.time() - t0
        elapsed = time.time() - start_time
        rate = i / elapsed if elapsed > 0 else 0
        eta_min = (len(remaining) - i) / rate / 60 if rate > 0 else 0
        print(f'✓ {success_count}/{len(chunks)} {dt:.1f}s (总{i}/{len(remaining)}, ETA {eta_min:.0f}min)', flush=True)

    # 最终保存
    save_progress(progress)
    elapsed = time.time() - start_time
    print(f'\n=== 索引完成 ===')
    print(f'  新索引 PDF: {len(remaining)} 个')
    print(f'  新 chunks: {total_new_chunks}')
    print(f'  用时: {elapsed:.0f}s')
    print(f'  输出: {INDEX_OUT}')
    print(f'  进度: {PROGRESS_FILE}')


if __name__ == '__main__':
    main()
