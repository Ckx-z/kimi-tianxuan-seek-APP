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
from pathlib import Path

# ---- 路径配置 ----
HERE = Path(__file__).parent.resolve()
ROOT = HERE.parent
PROJ = ROOT

PFDB = PROJ / 'experiment' / 'feedback_db.csv'
HINDEX = PROJ / 'experiment' / 'history' / 'index.json'

# 本地 PDF 检索路径（多个）
LOCAL_PDF_ROOTS = [
    PROJ / '知识库',                                # 用户整理过的本地知识库 (优先)
    Path(r'C:\Users\ckx\Desktop\实验\文章'),         # 原始实验 PDF 库
    Path(r'C:\Users\ckx\Desktop\科研\机器学习'),      # ML 文献库
]   


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
    """本地 PDF 关键词扫描 (浅层 - 不解 PDF 内容，仅文件名/路径)

    检索多个目录，按优先级返回（PROJ/知识库 优先）
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


def search(query_dict):
    """
    主入口
    query_dict = {
        'aldehyde_cas': '1300701-03-4',  # 可选
        'amine_cas': '14544-47-9',         # 可选
        'keywords': ['TAPT', 'CF3', '膜'],  # 可选
        'max_pdf_results': 5
    }
    """
    aldehyde_cas = query_dict.get('aldehyde_cas')
    amine_cas = query_dict.get('amine_cas')
    keywords = query_dict.get('keywords', [])

    results = {
        'feedback_matches': [],   # 直接相似的历史反馈
        'history_doc_matches': [],  # 涉及相同 CAS 的方案
        'pdf_keyword_matches': [],  # 文件名命中关键词的 PDF
    }

    # 1. 反馈库 CAS 精确匹配
    if aldehyde_cas or amine_cas:
        fb_matches = cas_search(aldehyde_cas, amine_cas)
        results['feedback_matches'] = fb_matches

    # 2. 历史方案索引匹配
    if aldehyde_cas or amine_cas:
        results['history_doc_matches'] = cas_to_smiles_candidates(aldehyde_cas, amine_cas)

    # 3. 关键词扫描本地 PDF (按文件名)
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
    if results.get('pdf_keyword_matches'):
        lines.append('## 本地文献（按文件名匹配）')
        for p in results['pdf_keyword_matches'][:max_items]:
            lines.append(f"- {p['name']}  路径: {p['path']}")
    return '\n'.join(lines) if lines else '(无匹配)'


if __name__ == '__main__':
    # 简单自测: ABCDEF 实验 A (TAPB+A6)
    res = search({
        'aldehyde_cas': '1300701-03-4',
        'amine_cas': '118727-34-7',
        'keywords': ['TAPT', 'CF3', 'terphenyl'],
        'max_pdf_results': 5
    })
    print('=== 自测: TAPB + A6 (从 ABCDEF 实验 A 出发) ===')
    print(format_results_for_prompt(res))
    print()
    # 自测 2: 候选 #2 (TFPT+H3)
    res2 = search({
        'aldehyde_cas': '443922-06-3',
        'amine_cas': '2569674-64-0',
        'keywords': ['TFPT', '酰肼', 'hydrazide', 'fluoroalkyl'],
        'max_pdf_results': 5
    })
    print('=== 自测: TFPT + H3 (候选 #2 第一份样例) ===')
    print(format_results_for_prompt(res2))
