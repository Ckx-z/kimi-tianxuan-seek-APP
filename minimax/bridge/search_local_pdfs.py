"""
bridge/search_local_pdfs.py
本地 PDF 文献检索 + 反馈库检索
==============================
输入: 单体对 (醛 CAS, 胺 CAS) + 可选: 关键词
输出: 排序后的相关案例 (历史方案/历史反馈/文献片段)
"""
import os
import re
import json
import csv
import sys
from pathlib import Path

# ---- 路径配置 ----
HERE = Path(__file__).parent.resolve()
ROOT = HERE.parent
PROJ = ROOT

PFDB = PROJ / 'experiment' / 'feedback_db.csv'
HINDEX = PROJ / 'experiment' / 'history' / 'index.json'

# 本地 PDF 检索路径(多个)
LOCAL_PDF_ROOTS = [
    PROJ / '知识库',                                # 用户整理过的本地知识库 (优先)
    Path(r'C:\Users\ckx\Desktop\实验\文章'),         # 原始实验 PDF 库
    Path(r'C:\Users\ckx\Desktop\科研\机器学习'),      # ML 文献库
]

# 知识库 embedding 索引
KB_INDEX = PROJ / 'bridge' / 'knowledge_index.jsonl'
# tianxuan-seek 全库索引 (5.8 GB, 282057 chunks, 2468 PDFs)
TIANXUAN_INDEX = PROJ / 'bridge' / 'knowledge_index_tianxuan.jsonl'


def load_feedback_db():
    """加载反馈 CSV 为 list of dict"""
    if not PFDB.exists():
        return []
    with open(PFDB, encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    return rows


def load_history_index():
    """加载历史方案索引"""
    if not HINDEX.exists():
        return {'documents': []}
    with open(HINDEX, encoding='utf-8') as f:
        return json.load(f)


def cas_search(aldehyde_cas=None, amine_cas=None, reactant_type='any'):
    """按 CAS 精确匹配历史反馈"""
    rows = load_feedback_db()
    matches = []
    for r in rows:
        if aldehyde_cas and r.get('醛CAS', '').strip() == str(aldehyde_cas).strip():
            matches.append({**r, '_match_type': 'exact_cas_aldehyde'})
        elif amine_cas and r.get('胺CAS', '').strip() == str(amine_cas).strip():
            matches.append({**r, '_match_type': 'exact_cas_amine'})
    return matches


def cas_to_smiles_candidates(aldehyde_cas=None, amine_cas=None):
    """从 history/index 找涉及特定 CAS 的方案"""
    hist = load_history_index()
    matched = []
    for d in hist.get('documents', []):
        tags = ' '.join(d.get('tags', [])).lower()
        node = d.get('node', '').lower()
        cas_str = f'{aldehyde_cas or ""} {amine_cas or ""}'.lower()
        if cas_str.strip() in tags + ' ' + node + ' ' + d.get('title', '').lower():
            matched.append(d)
    return matched


def keyword_search_local_pdfs(keywords, max_results=10):
    """本地 PDF 关键词扫描 (浅层 - 不解 PDF 内容,仅文件名/路径)

    检索多个目录,按优先级返回(PROJ/知识库 优先)
    """
    matches = []
    if not keywords:
        return matches
    kw_set = set(k.lower() for k in keywords if k)

    for pdf_root in LOCAL_PDF_ROOTS:
        if not pdf_root.exists():
            continue
        for pdf_path in pdf_root.rglob('*.pdf'):
            name_l = pdf_path.name.lower()
            # 命中关键词 (基于文件名)
            if any(kw in name_l for kw in kw_set):
                matches.append({
                    'path': str(pdf_path),
                    'name': pdf_path.name,
                    'match_type': 'filename_keyword',
                    'root': str(pdf_root),
                })
                if len(matches) >= max_results:
                    return matches
    return matches


def embedding_search(query_text, top_k=5, min_sim=0.5, sources=None):
    """embedding 检索, 支持核心知识库和 tianxuan 全库
    
    sources: list of str, 可选 'core' / 'tianxuan'. None = 全部.
    """
    index_files = []
    if sources is None or 'core' in sources:
        if KB_INDEX.exists():
            index_files.append(KB_INDEX)

    has_tianxuan = (sources is None or 'tianxuan' in sources) and TIANXUAN_INDEX.exists()

    # 计算 query embedding
    query_vec = _compute_query_embedding(query_text)
    if not query_vec:
        return []

    all_sims = []

    # 核心知识库: 线性扫描 JSONL
    if index_files:
        import math
        q_norm = math.sqrt(sum(x * x for x in query_vec)) + 1e-10
        for idx_file in index_files:
            with open(idx_file, encoding='utf-8') as f:
                for line in f:
                    r = json.loads(line)
                    v = r['vector']
                    dot = sum(a * b for a, b in zip(query_vec, v))
                    v_norm = math.sqrt(sum(x * x for x in v)) + 1e-10
                    sim = dot / (q_norm * v_norm)
                    if sim >= min_sim:
                        all_sims.append((sim, r))

    # tianxuan: 二进制快速检索
    if has_tianxuan:
        try:
            from fast_search import search_tianxuan
            fast_results = search_tianxuan(query_vec, top_k=top_k, min_sim=min_sim)
            all_sims.extend(fast_results)
        except ImportError:
            pass  # fast_search 不可用时跳过

    all_sims.sort(key=lambda x: x[0], reverse=True)
    return all_sims[:top_k]


def _compute_query_embedding(text):
    """调用 MiniMax embedding API 计算 query 向量"""
    try:
        api_key = os.environ.get('MINIMAX_API_KEY', '')
        if not api_key:
            return None
        import requests as req
        r = req.post(
            'https://api.minimax.chat/v1/embeddings',
            headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
            json={'model': 'embo-01', 'texts': [text], 'type': 'query'},
            timeout=30,
        )
        r.raise_for_status()
        j = r.json()
        return j['vectors'][0]
    except Exception as e:
        print(f'Embedding API 失败: {e}')
        return None


def search(query_dict):
    """
    主入口
    query_dict = {
        'aldehyde_cas': '1300701-03-4',  # 可选
        'amine_cas': '14544-47-9',         # 可选
        'keywords': ['TAPT', 'CF3', '膜'],  # 可选
        'query_text': '自然语言查询',          # 可选 (触发 embedding 检索)
        'max_pdf_results': 5
    }
    """
    aldehyde_cas = query_dict.get('aldehyde_cas')
    amine_cas = query_dict.get('amine_cas')
    keywords = query_dict.get('keywords', [])
    query_text = query_dict.get('query_text', '')

    results = {
        'feedback_matches': [],       # 直接相似的历史反馈
        'history_doc_matches': [],     # 涉及相同 CAS 的方案
        'embedding_matches': [],       # 核心知识库 embedding 检索
        'tianxuan_matches': [],        # tianxuan 全库 embedding 检索
        'pdf_keyword_matches': [],     # 文件名命中关键词的 PDF
    }

    # 1. 反馈库 CAS 精确匹配
    if aldehyde_cas or amine_cas:
        fb_matches = cas_search(aldehyde_cas, amine_cas)
        results['feedback_matches'] = fb_matches

    # 2. 历史方案索引匹配
    if aldehyde_cas or amine_cas:
        results['history_doc_matches'] = cas_to_smiles_candidates(aldehyde_cas, amine_cas)

    # 3. 核心知识库 embedding 检索
    if query_text:
        results['embedding_matches'] = embedding_search(
            query_text, top_k=query_dict.get('top_k_embedding', 5),
            sources=['core'])

    # 4. tianxuan 全库 embedding 检索 (高容量, 可能较慢)
    if query_text and query_dict.get('use_tianxuan', True):
        results['tianxuan_matches'] = embedding_search(
            query_text, top_k=query_dict.get('top_k_tianxuan', 10),
            sources=['tianxuan'], min_sim=query_dict.get('tianxuan_min_sim', 0.65))

    # 5. 关键词扫描本地 PDF (按文件名)
    if keywords:
        results['pdf_keyword_matches'] = keyword_search_local_pdfs(
            keywords, max_results=query_dict.get('max_pdf_results', 5))

    return results


def format_results_for_prompt(results, max_items=5):
    """格式化为可注入 LLM prompt 的字符串"""
    lines = []
    if results.get('feedback_matches'):
        lines.append('## 历史反馈匹配')
        for r in results['feedback_matches'][:max_items]:
            lines.append(
                f"- {r.get('方案编号', '?')} | 单体对 {r.get('醛CAS','')} + {r.get('胺CAS','')} | "
                f"Class {r.get('失败Class','')} | tianxuan {r.get('tianxuan_预测概率','')} | "
                f"现象: {r.get('失败现象描述', '')[:80]}"
            )
    if results.get('history_doc_matches'):
        lines.append('## 相关历史方案')
        for d in results['history_doc_matches'][:max_items]:
            lines.append(
                f"- {d.get('title', '?')} ({d.get('date','?')}) | "
                f"节点 {d.get('node','?')} | 路径: {d.get('path','?')} | "
                f"标签: {', '.join(d.get('tags', [])[:3])}"
            )
    if results.get('embedding_matches'):
        lines.append('## 核心知识库 RAG (embedding 检索)')
        for sim, r in results['embedding_matches'][:max_items]:
            lines.append(
                f"- [sim {sim:.3f}] {r['path']}\n"
                f"  {r['text'][:200]}"
            )
    if results.get('tianxuan_matches'):
        lines.append("## Tianxuan 全库 RAG (embedding 检索, {} hits)".format(len(results['tianxuan_matches'])))
        for sim, r in results['tianxuan_matches'][:max_items]:
            src = r.get('source', '')
            path_short = r['path'].split('\\')[-1] if '\\' in r['path'] else r['path'].split('/')[-1]
            lines.append(
                f"- [sim {sim:.3f}] {path_short}\n"
                f"  {r['text'][:200]}"
            )
    if results.get('pdf_keyword_matches'):
        lines.append('## 本地文献(按文件名匹配)')
        for p in results['pdf_keyword_matches'][:max_items]:
            lines.append(f"- {p['name']}  路径: {p['path']}")
    return '\n'.join(lines) if lines else '(无匹配)'


if __name__ == '__main__':
    # 简单自测: ABCDEF 实验 A (TAPB+A6)
    res = search({
        'aldehyde_cas': '1300701-03-4',
        'amine_cas': '118727-34-7',
        'keywords': ['TAPT', 'CF3', 'terphenyl'],
        'query_text': 'TAPB 苯胺 与三联苯 CF3 二醛反应 玻璃壁面 成膜',
        'max_pdf_results': 5,
        'top_k_embedding': 3,
    })
    print('=== 自测: TAPB + A6 (从 ABCDEF 实验 A 出发) ===')
    print(format_results_for_prompt(res))
    print()
    # 自测 2: 候选 #2 (TFPT+H3)
    res2 = search({
        'aldehyde_cas': '443922-06-3',
        'amine_cas': '2569674-64-0',
        'keywords': ['TFPT', '酰肼', 'hydrazide', 'fluoroalkyl'],
        'query_text': 'TFPT 三嗪醛 与 H3 长氟链酰肼 反应条件 温度 溶剂',
        'max_pdf_results': 5,
        'top_k_embedding': 3,
    })
    print('=== 自测: TFPT + H3 (候选 #2 第一份样例) ===')
    print(format_results_for_prompt(res2))
