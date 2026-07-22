# -*- coding: utf-8 -*-
"""
adapters/iterate_suggest.py
===========================
页⑤ 自然语言方案迭代 —— 编排器本体

流程（对应协作契约）:
  1. 读 App 侧 data/rag_export/records/ 中该 favorite 的实验记录
     （favorite_id 匹配的 + 游离记录中 CAS 匹配的也纳入）
  2. 读 data/favorites/<fav_id>.json 取醛/胺单体（SMILES/CAS/name）
  3. 模板化拼检索查询（不用 LLM 理解问题：用户问题原文 + 醛胺 CAS/name
     + 失败 outcome 直接拼进 query）
  4. 检索取证（降级链）:
       - 优先 search_local_pdfs.search() + format_results_for_prompt()
       - GraphRAG 图检索（query_graphrag）import/运行失败时静默跳过
  5. 拼 prompt，要求 LLM 输出严格 JSON 数组
     [{type: condition_adjust|new_candidate|literature, title, detail, evidence_refs}]
  6. llm_client.chat_completion(max_tokens=8000, timeout=120)
  7. 容错解析 JSON（提取第一个 [ 到最后一个 ]）
  8. 状态去重（status=rejected 的建议方向不再重复）后按 Schema 3 落盘
     data/rag_export/suggestions/sug_YYYYMMDD_NNN.json
  9. stdout 打印一行 JSON 摘要 {"written": [...], "count": N}

LLM 失败时不报错退出：写一条 type=literature 的降级建议（只含检索证据）。

用法:
  python minimax/adapters/iterate_suggest.py --favorite-id fav_20260722_001 \
      --question "这组单体上次失败了，下次怎么调条件"
  python minimax/adapters/iterate_suggest.py --question "..."   # 省略 = 全部实验记录

退出码: 0 成功；非 0 失败（stderr 给人读的错误）。
"""
import argparse
import datetime
import json
import re
import sys
import traceback
from pathlib import Path

# ---- sys.path 引导：让 bridge/ 下的模块可按裸名 import ----
HERE = Path(__file__).parent.resolve()          # minimax/adapters
PROJ = HERE.parent                              # minimax/
BRIDGE = PROJ / 'bridge'
for p in (str(HERE), str(BRIDGE)):
    if p not in sys.path:
        sys.path.insert(0, p)

# App 侧根目录（默认钉死，可用 --app-root 覆盖以便测试）
DEFAULT_APP_ROOT = Path(r'C:\Users\ckx\Desktop\全新机器学习实验')

SCHEMA_VERSION = '1.0'
LOCAL_TZ = datetime.timezone(datetime.timedelta(hours=8))  # 契约要求 +08:00


def now_iso() -> str:
    """当前时间 ISO 8601 带 +08:00 时区"""
    return datetime.datetime.now(LOCAL_TZ).replace(microsecond=0).isoformat()


def make_batch_id() -> str:
    """生成本次运行的批次号 batch_YYYYMMDD_HHMMSS（同次运行所有建议共用）"""
    return 'batch_' + datetime.datetime.now(LOCAL_TZ).strftime('%Y%m%d_%H%M%S')


def today_str() -> str:
    return datetime.datetime.now(LOCAL_TZ).strftime('%Y%m%d')


def err_exit(msg: str, code: int = 1):
    """人读错误走 stderr，非 0 退出"""
    print(f'[iterate_suggest] 错误: {msg}', file=sys.stderr)
    sys.exit(code)


# ---------------------------------------------------------------- 数据读取

def load_json_dir(d: Path):
    """读取目录下全部 .json（跳过 example.json 与解析失败文件）"""
    items = []
    if not d.exists():
        return items
    for p in sorted(d.glob('*.json')):
        if p.name.startswith('example'):
            continue
        try:
            items.append(json.loads(p.read_text(encoding='utf-8')))
        except Exception as e:
            print(f'[iterate_suggest] 跳过无法解析的文件 {p.name}: {e}', file=sys.stderr)
    return items


def load_favorite(app_root: Path, fav_id: str):
    """读 data/favorites/<fav_id>.json；不存在则报错退出"""
    fp = app_root / 'data' / 'favorites' / f'{fav_id}.json'
    if not fp.exists():
        err_exit(f'收藏条目不存在: {fp}', code=2)
    try:
        return json.loads(fp.read_text(encoding='utf-8'))
    except Exception as e:
        err_exit(f'收藏条目解析失败: {fp}: {e}', code=2)


def select_records(records, fav_id, favorite):
    """筛选相关实验记录:

    - 指定 favorite 时: favorite_id 匹配的记录 + 游离记录（favorite_id 为空）
      中醛/胺 CAS 与该 favorite 单体匹配的记录
    - 未指定 favorite 时: 全部记录
    """
    if not fav_id:
        return records
    ald_cas = (favorite.get('aldehyde') or {}).get('cas', '').strip()
    ami_cas = (favorite.get('amine') or {}).get('cas', '').strip()
    picked = []
    for r in records:
        if r.get('record_type') != 'experiment_record':
            continue
        if r.get('favorite_id') == fav_id:
            picked.append(r)
            continue
        # 游离记录: CAS 任一匹配即纳入
        if not r.get('favorite_id'):
            r_ald = (r.get('aldehyde') or {}).get('cas', '').strip()
            r_ami = (r.get('amine') or {}).get('cas', '').strip()
            if (ald_cas and r_ald == ald_cas) or (ami_cas and r_ami == ami_cas):
                picked.append(r)
    return picked


# ---------------------------------------------------------------- 检索取证

def monomer_keywords(monomer: dict):
    """从单体对象提取检索关键词（name 拆词 + CAS）"""
    kws = []
    name = (monomer or {}).get('name', '') or ''
    # 名字里常带缩写代号（如 "A6 4,4''-双(三氟甲基)..."），取首个 token 作为代号
    first = name.split()[0] if name.split() else ''
    if first and re.fullmatch(r'[A-Za-z0-9\-]{2,12}', first):
        kws.append(first)
    cas = (monomer or {}).get('cas', '').strip()
    if cas:
        kws.append(cas)
    return kws


def build_query_text(question: str, aldehyde: dict, amine: dict, records) -> str:
    """模板化拼检索查询：用户问题原文 + 醛胺 name/CAS + 失败记录概况"""
    parts = [question]
    for label, m in (('醛', aldehyde), ('胺', amine)):
        name = (m or {}).get('name', '')
        cas = (m or {}).get('cas', '')
        if name or cas:
            parts.append(f'{label}单体 {name} CAS {cas}'.strip())
    failed = [r for r in records if r.get('outcome') in ('failed', 'partial')]
    for r in failed[-3:]:  # 最近几条失败/部分成功记录的现象一并拼入
        cond = r.get('conditions') or {}
        seg = (f"实验{r.get('experiment_no', '')} outcome={r.get('outcome')} "
               f"失败分类={r.get('failure_class') or '未知'} "
               f"溶剂={cond.get('solvent_1') or cond.get('solvent', '')} "
               f"催化={cond.get('catalyst', '')} 温度={cond.get('temperature_c', '')} "
               f"现象={r.get('strength', '')} {r.get('notes', '')}")
        parts.append(seg.strip())
    return '\n'.join(p for p in parts if p)


# ---------------------------------------------------------------- failure 专家语料

def _split_md_sections(md_text: str):
    """按 markdown 二级标题（## ）切块，返回 [(标题, 整块文本), ...]"""
    sections = []
    cur_title, cur_lines = None, []
    for line in md_text.splitlines():
        if line.startswith('## '):
            if cur_title is not None:
                sections.append((cur_title, '\n'.join(cur_lines).strip()))
            cur_title = line[3:].strip()
            cur_lines = [line]
        elif cur_title is not None:
            cur_lines.append(line)
    if cur_title is not None:
        sections.append((cur_title, '\n'.join(cur_lines).strip()))
    return sections


def retrieve_failure_corpus(records, app_root: Path, favorite=None):
    """第三路取证：内部失败处置专家语料（降级链，任何失败静默跳过）

    - failure_criteria.md：按实验记录的 failure_class（A~G）抽对应 Class 段落
    - failure_playbook.md：按实验号（experiment_no，如 A1/D7）抽对应小节；
      有命中时附带「全局观察」小节
    返回 (证据文本, 命中段数)；无命中返回 ('', 0)
    """
    blocks = []
    n_hit = 0
    exp_dir = Path(app_root) / 'minimax' / 'experiment'

    # 1. failure_criteria.md：按 failure_class 抽 Class 段落
    try:
        classes = set()
        for r in (records or []):
            fc = str(r.get('failure_class') or '').strip()
            # 先剥掉 "CLASS" 前缀再取字母，避免 'Class A' 误匹配到 C
            m = re.search(r'([A-G])', re.sub(r'CLASS', '', fc.upper()))
            if m:
                classes.add(m.group(1))
        if classes:
            md = (exp_dir / 'failure_criteria.md').read_text(encoding='utf-8')
            for title, body in _split_md_sections(md):
                m = re.match(r'Class\s+([A-G])', title)
                if m and m.group(1) in classes:
                    blocks.append(body)
                    n_hit += 1
    except Exception as e:
        print(f'[iterate_suggest] failure_criteria 读取失败（降级继续）: {e}',
              file=sys.stderr)

    # 2. failure_playbook.md：按实验号 / favorite 抽对应小节
    try:
        exp_keys = set()
        for r in (records or []):
            no = str(r.get('experiment_no') or '').strip()
            if no:
                exp_keys.add(no.upper())
        # favorite 里也可能带实验号字段
        for k in ('experiment_no', 'exp_no', 'plan_no'):
            no = str((favorite or {}).get(k) or '').strip()
            if no:
                exp_keys.add(no.upper())
        if exp_keys:
            md = (exp_dir / 'failure_playbook.md').read_text(encoding='utf-8')
            sections = _split_md_sections(md)
            matched = False
            for title, body in sections:
                first = title.split()[0].upper() if title.split() else ''
                if first in exp_keys:
                    blocks.append(body)
                    n_hit += 1
                    matched = True
            if matched:
                # 有具体实验命中时附带「全局观察」共同问题小节
                for title, body in sections:
                    if title.startswith('全局观察'):
                        blocks.append(body)
                        n_hit += 1
                        break
    except Exception as e:
        print(f'[iterate_suggest] failure_playbook 读取失败（降级继续）: {e}',
              file=sys.stderr)

    if not blocks:
        return '', 0
    text = '## 内部失败处置经验（failure_criteria / failure_playbook 专家语料）\n\n' \
           + '\n\n'.join(blocks)
    return text, n_hit


def retrieve_evidence(query_text: str, aldehyde: dict, amine: dict,
                      records=None, app_root=None, favorite=None):
    """检索取证（降级链）。返回 (证据文本, literature_refs, 图节点可引用 ID 列表)

    - 优先 search_local_pdfs.search() 五路召回 + format_results_for_prompt()
    - GraphRAG 图检索 import/运行失败（如缺 networkx）时静默跳过
    - GraphRAG 命中反应节点后接 graphrag_v2 多跳 BFS 路径（失败静默跳过）
    - failure 专家语料（failure_criteria / failure_playbook）按 Class/实验号注入
    - 全程 stderr 打印每路实际命中数，便于体检核对
    """
    evidence_blocks = []
    lit_refs = []
    graph_ref_ids = []  # 图检索命中的反应/文献节点 ID（纳入引用白名单）

    # 1. 本地 PDF / 反馈库 / embedding 检索（五路召回）
    try:
        import search_local_pdfs
        results = search_local_pdfs.search({
            'aldehyde_cas': (aldehyde or {}).get('cas') or None,
            'amine_cas': (amine or {}).get('cas') or None,
            'keywords': monomer_keywords(aldehyde) + monomer_keywords(amine),
            'query_text': query_text,
            'max_pdf_results': 5,
            'top_k_embedding': 5,
        })
        evidence_blocks.append(search_local_pdfs.format_results_for_prompt(results))
        # 五路召回各自命中数日志
        print('[iterate_suggest] 五路召回命中: '
              f'feedback={len(results.get("feedback_matches") or [])} '
              f'history_doc={len(results.get("history_doc_matches") or [])} '
              f'embedding={len(results.get("embedding_matches") or [])} '
              f'tianxuan={len(results.get("tianxuan_matches") or [])} '
              f'pdf_keyword={len(results.get("pdf_keyword_matches") or [])}',
              file=sys.stderr)
        # 收集文献引用（供 evidence_refs 使用）
        for p in (results.get('pdf_keyword_matches') or [])[:5]:
            lit_refs.append({'kind': 'literature', 'ref': p.get('name', ''),
                             'note': '本地文献文件名命中'})
        for sim, r in (results.get('embedding_matches') or [])[:3]:
            ref = r.get('path', '').split('\\')[-1].split('/')[-1]
            lit_refs.append({'kind': 'literature', 'ref': ref,
                             'note': f'核心知识库 embedding 命中 sim={sim:.3f}'})
        for sim, r in (results.get('tianxuan_matches') or [])[:3]:
            ref = r.get('path', '').split('\\')[-1].split('/')[-1]
            lit_refs.append({'kind': 'literature', 'ref': ref,
                             'note': f'tianxuan 全库命中 sim={sim:.3f}'})
    except Exception as e:
        # 检索整体失败不阻断流程（LLM 仍可基于实验记录给建议）
        print(f'[iterate_suggest] search_local_pdfs 检索失败（降级继续）: {e}',
              file=sys.stderr)

    # 2. GraphRAG 图检索（可选，失败静默跳过）
    try:
        import query_graphrag
        # rerank 点亮状态：HAS_EMBED 为 True 且文献有命中时 rerank 实际生效
        print(f'[iterate_suggest] GraphRAG rerank: HAS_EMBED='
              f'{getattr(query_graphrag, "HAS_EMBED", False)}', file=sys.stderr)
        gres = query_graphrag.query(query_text)
        print(f'[iterate_suggest] GraphRAG 图检索命中: '
              f'reactions={len(gres.get("reactions") or [])} '
              f'literatures={len(gres.get("literatures") or [])}',
              file=sys.stderr)
        lines = ['## GraphRAG 图检索']
        for h in (gres.get('reactions') or [])[:5]:
            d = h['data']
            graph_ref_ids.append(str(h['id']))
            lines.append(
                f"- [{h['score']}★] 醛 {d.get('aldehyde_name','?')} + 胺 {d.get('amine_name','?')} | "
                f"溶剂 {d.get('solvent','?')} | 温度 {d.get('temperature','?')} | "
                f"产物 {d.get('outcome','?')}")
        for h in (gres.get('literatures') or [])[:5]:
            d = h['data']
            graph_ref_ids.append(str(h['id']))
            lines.append(
                f"- [{h['score']}★] {h['id']} | {d.get('journal','?')} | "
                f"{str(d.get('innovation',''))[:120]}")
            lit_refs.append({'kind': 'literature', 'ref': str(h['id']),
                             'note': f"GraphRAG 文献节点 {d.get('journal','')}"})
        if len(lines) > 1:
            evidence_blocks.append('\n'.join(lines))

        # 2b. 多跳 BFS：反应→溶剂/催化剂→同类成功反应→文献（import/运行失败静默跳过）
        try:
            from graphrag_v2.reasoning import multi_hop_paths, format_paths
            # 注意：用 graph.pkl（v1 图）跑多跳，与 graph_v2.pkl 节点 ID 可能不一致
            G = query_graphrag.load_graph()
            start_ids = [str(h['id']) for h in (gres.get('reactions') or [])[:3]
                         if str(h['id']) in G]
            if start_ids:
                paths = multi_hop_paths(G, start_ids, max_hops=3, max_paths=10)
                print(f'[iterate_suggest] 多跳 BFS: 起点 {len(start_ids)} 个反应节点, '
                      f'路径 {len(paths)} 条', file=sys.stderr)
                if paths:
                    evidence_blocks.append(
                        '## GraphRAG 多跳推理路径（反应→溶剂/催化剂→同类反应→文献）\n'
                        + format_paths(G, paths, max_paths=5))
        except Exception as e:
            # graphrag_v2 不可 import / 图结构不符等：静默跳过
            print(f'[iterate_suggest] 多跳 BFS 跳过（降级继续）: {e}',
                  file=sys.stderr)
    except Exception:
        # 缺 networkx / graph.pkl 不存在等情况：静默跳过
        pass

    # 3. failure 专家语料（按 failure_class / 实验号注入，失败静默跳过）
    try:
        fc_text, fc_n = retrieve_failure_corpus(records, app_root, favorite)
        print(f'[iterate_suggest] failure 专家语料命中: {fc_n} 段', file=sys.stderr)
        if fc_text:
            evidence_blocks.append(fc_text)
    except Exception as e:
        print(f'[iterate_suggest] failure 语料注入失败（降级继续）: {e}',
              file=sys.stderr)

    text = '\n\n'.join(b for b in evidence_blocks if b and b != '(无匹配)')
    return text or '(本次检索无匹配证据)', lit_refs, graph_ref_ids


# ---------------------------------------------------------------- 状态去重

def load_rejected_directions(sug_dir: Path, fav_id):
    """扫描 suggestions/ 已有文件，收集 status=rejected 的建议方向（去重用）

    返回 list[str]，每条为可读的"方向描述"（type + 标题/调整字段/候选 CAS）
    """
    rejected = []
    for s in load_json_dir(sug_dir):
        if s.get('record_type') != 'suggestion':
            continue
        if s.get('status') != 'rejected':
            continue
        if fav_id and s.get('favorite_id') not in (fav_id, None):
            continue
        t = s.get('type', '')
        payload = s.get('payload') or {}
        if t == 'condition_adjust':
            fields = '、'.join(str(a.get('field', ''))
                               for a in payload.get('adjustments', []))
            rejected.append(f'condition_adjust 调整字段: {fields}')
        elif t == 'new_candidate':
            ald = (payload.get('aldehyde') or {}).get('cas', '')
            ami = (payload.get('amine') or {}).get('cas', '')
            rejected.append(f'new_candidate 候选对: {ald} + {ami}')
        else:
            rejected.append(f"{t}: {payload.get('title', '')}")
    return rejected


def is_rejected_direction(item: dict, rejected) -> bool:
    """判断 LLM 产出的一条建议是否命中已否决方向（粗粒度去重）"""
    blob = json.dumps(item, ensure_ascii=False)
    for r in rejected:
        # 方向描述里的关键片段（字段名/CAS）若出现在新建议里则视为重复
        for token in re.split(r'[、:：\s]+', r):
            token = token.strip()
            if len(token) >= 3 and token in blob:
                return True
    return False


# ---------------------------------------------------------------- LLM

def build_messages(question, aldehyde, amine, records, evidence_text, rejected,
                   max_n=2):
    """拼 prompt：证据文本 + 实验记录 + 严格 JSON 输出要求（最多 max_n 条）"""
    sys_prompt = (
        '你是 COF（共价有机框架）成膜实验的迭代顾问。'
        '基于用户的历史实验记录与检索到的文献证据，给出下一步可操作的实验建议。'
        f'最多给 {max_n} 条最有价值的建议，宁缺毋滥。'
        '只输出一个严格 JSON 数组，不要输出任何其他文字、不要 markdown 代码块。'
        '数组元素格式: '
        '{"type": "condition_adjust|new_candidate|literature", '
        '"title": "一句话标题", '
        '"detail": "具体可操作的建议内容（条件调整需写明 字段/原值/改为/理由；'
        '新候选需写明醛胺 CAS 或名称及理由）", '
        '"evidence_refs": [{"kind": "experiment_record|literature|prediction", '
        '"ref": "rec_xxx 或文献名或 DOI", "note": "一句话说明"}], '
        '"confidence": {"level": "high|medium|low", '
        '"reason": "一句话自评理由（依据证据充分程度）"}}'
        '注意：kind=experiment_record 时 ref 必须原样使用下方'
        '「可引用的实验记录 ID」列表中的真实 record_id（形如 rec_YYYYMMDD_NNN），'
        '不要写 "1★"、"实验1" 之类的自然语言标记。'
        'kind=literature 时 ref 必须来自下方检索证据中真实出现的文献名/节点 ID，'
        '严禁编造引用；每条建议必须给出 confidence 自评。'
    )

    rec_lines = []
    for r in records:
        cond = r.get('conditions') or {}
        rec_lines.append(
            f"- {r.get('record_id')} (实验编号 {r.get('experiment_no', '?')}, "
            f"{r.get('date', '?')}): outcome={r.get('outcome')}, "
            f"failure_class={r.get('failure_class')}, "
            f"条件={json.dumps(cond, ensure_ascii=False)}, "
            f"现象/强度={r.get('strength', '')}, 备注={r.get('notes', '')}")
    records_text = '\n'.join(rec_lines) if rec_lines else '(无关联实验记录)'

    rejected_text = ('\n'.join(f'- {x}' for x in rejected)
                     if rejected else '(无)')
    # 可引用 record_id 白名单：显式写进 prompt，约束 LLM 引用真实 ID
    rec_ids = [r.get('record_id') for r in records if r.get('record_id')]
    rec_ids_text = ('\n'.join(f'- {rid}' for rid in rec_ids)
                    if rec_ids else '(无)')
    user_prompt = f"""## 用户问题
{question}

## 单体对
醛: {json.dumps(aldehyde or {}, ensure_ascii=False)}
胺: {json.dumps(amine or {}, ensure_ascii=False)}

## 相关实验记录
{records_text}

## 可引用的实验记录 ID（evidence_refs 中 kind=experiment_record 的 ref 必须从中原样选择）
{rec_ids_text}

## 检索到的证据
{evidence_text}

## 已被用户否决的建议方向（不要重复）
{rejected_text}

请输出 1~{max_n} 条建议的 JSON 数组（最多 {max_n} 条最有价值的建议）。"""
    return [{'role': 'system', 'content': sys_prompt},
            {'role': 'user', 'content': user_prompt}]


def parse_llm_json(content: str):
    """容错解析 LLM 输出：提取第一个 [ 到最后一个 ] 之间的 JSON 数组"""
    i, j = content.find('['), content.rfind(']')
    if i < 0 or j <= i:
        return None
    try:
        data = json.loads(content[i:j + 1])
        return data if isinstance(data, list) else None
    except Exception:
        return None


# ---------------------------------------------------------------- 落盘

def normalize_payload(item: dict):
    """把 LLM 产出的一条建议规整为 Schema 3 的 (type, payload)"""
    t = item.get('type') or 'literature'
    if t not in ('condition_adjust', 'new_candidate', 'literature'):
        t = 'literature'
    title = str(item.get('title', '')).strip()
    detail = str(item.get('detail', '')).strip()
    if t == 'condition_adjust':
        adjs = item.get('adjustments')
        if not isinstance(adjs, list) or not adjs:
            # LLM 未给结构化 adjustments 时，退化为单条文本调整
            adjs = [{'field': '', 'from': '', 'to': detail, 'rationale': title}]
        norm = []
        for a in adjs:
            if isinstance(a, dict):
                norm.append({'field': str(a.get('field', '')),
                             'from': str(a.get('from', '')),
                             'to': str(a.get('to', '')),
                             'rationale': str(a.get('rationale', ''))})
        return t, {'title': title, 'adjustments': norm or
                   [{'field': '', 'from': '', 'to': detail, 'rationale': title}]}
    if t == 'new_candidate':
        return t, {'title': title,
                   'aldehyde': item.get('aldehyde') or {},
                   'amine': item.get('amine') or {},
                   'rationale': detail}
    return 'literature', {'title': title, 'detail': detail}


def _correct_rec_ref(ref: str, whitelist):
    """对非白名单的实验记录引用做模糊匹配纠正。

    匹配策略（依次尝试）：
      1. 忽略大小写/空白后完全相等
      2. 互为子串（如 LLM 写 "rec_20260722" 漏了序号）
      3. 数字序列相等（如 "20260722-1" 与 "rec_20260722_001"）
    返回纠正后的 record_id；匹配不上返回 None。
    """
    ref_norm = re.sub(r'\s+', '', str(ref)).lower()
    if not ref_norm:
        return None
    digits = re.findall(r'\d+', ref_norm)
    for cand in whitelist:
        cand_norm = cand.lower()
        if ref_norm == cand_norm:
            return cand
    for cand in whitelist:
        cand_norm = cand.lower()
        if len(ref_norm) >= 8 and (ref_norm in cand_norm or cand_norm in ref_norm):
            return cand
    if digits:
        for cand in whitelist:
            cand_digits = re.findall(r'\d+', cand.lower())
            if cand_digits == digits:
                return cand
    return None


def _correct_lit_ref(ref: str, whitelist):
    """对非白名单的文献/图节点引用做模糊匹配纠正。

    匹配策略（依次尝试）：
      1. 忽略大小写/空白后完全相等
      2. 互为子串（长度 ≥6 防误配）
    返回纠正后的白名单条目；匹配不上返回 None。
    """
    ref_norm = re.sub(r'\s+', '', str(ref)).lower()
    if not ref_norm:
        return None
    for cand in whitelist:
        if ref_norm == re.sub(r'\s+', '', cand).lower():
            return cand
    for cand in whitelist:
        cand_norm = re.sub(r'\s+', '', cand).lower()
        if len(ref_norm) >= 6 and (ref_norm in cand_norm or cand_norm in ref_norm):
            return cand
    return None


def normalize_evidence(item: dict, records, lit_refs, graph_ref_ids=None):
    """规整 evidence_refs（波次 2 白名单校验）。

    白名单 = 可引用实验记录 ID + retrieve_evidence 返回的文献引用 + 图节点 ID。
    - experiment_record：按白名单模糊纠正；匹配不上整条剔除进 unverified_refs
    - literature：大小写/空白/子串模糊纠正；匹配不上整条剔除进 unverified_refs
      （绝不让编造引用静默通过）
    返回 (refs, unverified_refs, n_valid)
      n_valid = 落在白名单内的有效证据条数（用于 confidence 规则校验）
    """
    rec_whitelist = [r.get('record_id') for r in records if r.get('record_id')]
    lit_whitelist = ([str(e.get('ref', '')) for e in (lit_refs or [])
                      if e.get('ref')]
                     + [str(x) for x in (graph_ref_ids or []) if x])
    refs = []
    unverified = []
    n_valid = 0
    for e in (item.get('evidence_refs') or []):
        if not isinstance(e, dict):
            continue
        kind = e.get('kind')
        if kind not in ('experiment_record', 'literature', 'prediction'):
            continue
        ref = str(e.get('ref', ''))
        note = str(e.get('note', ''))
        if kind == 'experiment_record':
            if ref in rec_whitelist:
                n_valid += 1
            elif rec_whitelist:
                fixed = _correct_rec_ref(ref, rec_whitelist)
                if fixed:
                    # 模糊纠正为真实 record_id（LLM 常写自然语言标记）
                    note = (note + f'（原引用「{ref}」已自动纠正）').strip()
                    ref = fixed
                    n_valid += 1
                else:
                    # 匹配不上：整条剔除，记录原文，不让编造引用静默通过
                    unverified.append({'kind': kind, 'ref': ref, 'note': note})
                    continue
        elif kind == 'literature':
            if lit_whitelist:
                if ref in lit_whitelist:
                    n_valid += 1
                else:
                    fixed = _correct_lit_ref(ref, lit_whitelist)
                    if fixed:
                        note = (note + f'（原引用「{ref}」已自动纠正）').strip()
                        ref = fixed
                        n_valid += 1
                    else:
                        unverified.append({'kind': kind, 'ref': ref, 'note': note})
                        continue
            # 无任何文献白名单（检索全失败）时文献引用一律不可信，剔除
            else:
                unverified.append({'kind': kind, 'ref': ref, 'note': note})
                continue
        refs.append({'kind': kind, 'ref': ref, 'note': note})
    if not refs:
        # 兜底：引用相关实验记录 + 检索文献（均为白名单内真实 ID）
        refs = [{'kind': 'experiment_record', 'ref': r.get('record_id', ''),
                 'note': '相关历史实验记录'} for r in records[:2]]
        refs.extend(lit_refs[:2])
        n_valid = len(refs)
    return refs, unverified, n_valid


def normalize_confidence(item: dict, n_valid: int):
    """规整 confidence 自评（波次 2）。

    - 接受 {"level": ..., "reason": ...} 或裸字符串；非法等级默认 medium
    - 规则校验：0 条有效证据（白名单内）的建议强制降为 low 并标注
    返回 {'level': 'high|medium|low', 'reason': str}
    """
    raw = item.get('confidence')
    if isinstance(raw, dict):
        level = str(raw.get('level', '')).strip().lower()
        reason = str(raw.get('reason', '')).strip()
    else:
        level = str(raw or '').strip().lower()
        reason = ''
    if level not in ('high', 'medium', 'low'):
        level = 'medium'
        reason = (reason + '（等级缺失/非法，默认 medium）').strip()
    if n_valid == 0:
        # 0 条有效证据：强制降为 low 并标注
        level = 'low'
        reason = (reason + '（无白名单内有效证据，置信度强制降为 low）').strip()
    return {'level': level, 'reason': reason}


def next_sug_ids(sug_dir: Path, n: int):
    """分配 n 个连续 sug_YYYYMMDD_NNN 编号（扫描已有文件取最大序号）"""
    date = today_str()
    max_n = 0
    if sug_dir.exists():
        for p in sug_dir.glob(f'sug_{date}_*.json'):
            m = re.match(rf'sug_{date}_(\d+)\.json', p.name)
            if m:
                max_n = max(max_n, int(m.group(1)))
    return [f'sug_{date}_{max_n + k + 1:03d}' for k in range(n)]


def _monomer_ok(obj) -> bool:
    """单体对象是否可采纳（含非空 smiles）"""
    return (isinstance(obj, dict)
            and isinstance(obj.get('smiles'), str)
            and bool(obj['smiles'].strip()))


def _inject_monomers(payload: dict, aldehyde, amine):
    """游离建议兜底：payload 缺少合法醛/胺单体时，写入从最近实验记录带出的单体。

    只对缺 smiles 的字段做填充——new_candidate 型建议若 LLM 已给出带 smiles 的
    新候选单体则保留原样，不覆盖其语义。
    """
    if aldehyde and not _monomer_ok(payload.get('aldehyde')):
        payload['aldehyde'] = dict(aldehyde)
    if amine and not _monomer_ok(payload.get('amine')):
        payload['amine'] = dict(amine)
    return payload


def write_suggestions(sug_dir: Path, items, fav_id, records, lit_refs, rejected,
                      batch, max_n=2, aldehyde=None, amine=None, graph_ref_ids=None):
    """状态去重后按 Schema 3 落盘，返回写出的 suggestion_id 列表

    batch: 本次运行批次号，写入每条建议的 "batch" 字段（Schema 3 可选字段）
    max_n: 每次运行最多写出的建议条数
    aldehyde/amine: 游离路径（fav_id 为空）时从最近实验记录带出的单体对象，
      写入每条建议 payload（所有 type），保证 adopt_suggestion 可直接采纳
    graph_ref_ids: 图检索命中的节点 ID（纳入 evidence_refs 白名单）
    """
    sug_dir.mkdir(parents=True, exist_ok=True)
    kept = []
    for item in items:
        if len(kept) >= max_n:
            break  # 限量：最多 max_n 条
        if not isinstance(item, dict):
            continue
        if is_rejected_direction(item, rejected):
            print(f'[iterate_suggest] 跳过与已否决方向重复的建议: '
                  f'{item.get("title", "")}', file=sys.stderr)
            continue
        kept.append(item)
    ids = next_sug_ids(sug_dir, len(kept))
    written = []
    for sid, item in zip(ids, kept):
        t, payload = normalize_payload(item)
        if not fav_id:
            # 游离建议：写入单体对象，修复 adopt_suggestion 无法解析单体的问题
            payload = _inject_monomers(payload, aldehyde, amine)
        refs, unverified, n_valid = normalize_evidence(
            item, records, lit_refs, graph_ref_ids)
        # 置信度自评 + 规则校验（0 条有效证据强制 low）
        payload['confidence'] = normalize_confidence(item, n_valid)
        if unverified:
            # 匹配不上白名单的引用：整条剔除并记录原文，便于人工复核
            payload['unverified_refs'] = unverified
            print(f'[iterate_suggest] {sid}: 剔除 {len(unverified)} 条'
                  f'未通过白名单校验的引用: '
                  f'{[u.get("ref") for u in unverified]}', file=sys.stderr)
        doc = {
            'schema_version': SCHEMA_VERSION,
            'record_type': 'suggestion',
            'suggestion_id': sid,
            'favorite_id': fav_id,
            'batch': batch,
            'type': t,
            'payload': payload,
            'evidence_refs': refs,
            'created_at': now_iso(),
            'status': 'new',
        }
        (sug_dir / f'{sid}.json').write_text(
            json.dumps(doc, ensure_ascii=False, indent=1), encoding='utf-8')
        written.append(sid)
    return written


def write_fallback_suggestion(sug_dir: Path, fav_id, question, evidence_text,
                              lit_refs, records, reason, batch,
                              aldehyde=None, amine=None):
    """LLM 失败/输出无法解析时的降级：写一条 type=literature 建议（只含检索证据）

    降级建议固定 1 条，同样带本次运行的 batch 批次号。
    游离路径（fav_id 为空）时同样注入单体对象，保证可采纳。
    """
    sug_dir.mkdir(parents=True, exist_ok=True)
    sid = next_sug_ids(sug_dir, 1)[0]
    refs = list(lit_refs[:5])
    refs.extend({'kind': 'experiment_record', 'ref': r.get('record_id', ''),
                 'note': '相关历史实验记录'} for r in records[:3])
    payload = {
        'title': f'LLM 暂不可用，以下为针对「{question[:30]}」检索到的原始证据',
        'detail': f'（降级建议，生成原因: {reason}）\n\n{evidence_text[:3000]}',
    }
    if not fav_id:
        payload = _inject_monomers(payload, aldehyde, amine)
    doc = {
        'schema_version': SCHEMA_VERSION,
        'record_type': 'suggestion',
        'suggestion_id': sid,
        'favorite_id': fav_id,
        'batch': batch,
        'type': 'literature',
        'payload': payload,
        'evidence_refs': refs or [{'kind': 'literature', 'ref': '',
                                   'note': '本次检索无匹配证据'}],
        'created_at': now_iso(),
        'status': 'new',
    }
    (sug_dir / f'{sid}.json').write_text(
        json.dumps(doc, ensure_ascii=False, indent=1), encoding='utf-8')
    return [sid]


# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser(description='页⑤ 自然语言方案迭代编排器')
    ap.add_argument('--favorite-id', default=None,
                    help='收藏条目 id；省略表示用全部实验记录')
    ap.add_argument('--question', required=True, help='用户问题原文')
    ap.add_argument('--app-root', default=str(DEFAULT_APP_ROOT),
                    help='App 侧根目录（默认钉死，测试可覆盖）')
    ap.add_argument('--max', type=int, default=2, dest='max_n',
                    help='每次运行最多写出的建议条数（默认 2）')
    args = ap.parse_args()

    max_n = max(1, args.max_n)  # 至少 1 条，防误传 0/负数
    batch = make_batch_id()     # 本次运行批次号：batch_YYYYMMDD_HHMMSS

    app_root = Path(args.app_root)
    records_dir = app_root / 'data' / 'rag_export' / 'records'
    sug_dir = app_root / 'data' / 'rag_export' / 'suggestions'

    # 1. 读实验记录 + 收藏单体
    favorite = None
    aldehyde = amine = None
    if args.favorite_id:
        favorite = load_favorite(app_root, args.favorite_id)
        aldehyde = favorite.get('aldehyde') or {}
        amine = favorite.get('amine') or {}
    all_records = load_json_dir(records_dir)
    records = select_records(all_records, args.favorite_id, favorite)
    if not records:
        print('[iterate_suggest] 警告: 未找到关联实验记录，将仅基于检索证据生成',
              file=sys.stderr)
    # 未指定 favorite 时，从最近一条记录带出单体信息（便于检索）
    if not args.favorite_id and records:
        aldehyde = records[-1].get('aldehyde') or {}
        amine = records[-1].get('amine') or {}

    # 2. 模板化拼检索查询
    query_text = build_query_text(args.question, aldehyde, amine, records)

    # 3. 检索取证（降级链，含 failure 专家语料与多跳 BFS）
    evidence_text, lit_refs, graph_ref_ids = retrieve_evidence(
        query_text, aldehyde, amine,
        records=records, app_root=app_root, favorite=favorite)

    # 4. 状态去重：收集已否决方向
    rejected = load_rejected_directions(sug_dir, args.favorite_id)

    # 5. LLM 生成建议（失败则写降级建议）
    try:
        from llm_client import chat_completion
        messages = build_messages(args.question, aldehyde, amine, records,
                                  evidence_text, rejected, max_n=max_n)
        content, provider = chat_completion(messages, max_tokens=8000, timeout=120)
        print(f'[iterate_suggest] LLM 端点: {provider}', file=sys.stderr)
        items = parse_llm_json(content)
        if items is None:
            raise ValueError('LLM 输出无法解析为 JSON 数组')
    except Exception as e:
        print(f'[iterate_suggest] LLM 失败，写降级建议: {e}', file=sys.stderr)
        written = write_fallback_suggestion(
            sug_dir, args.favorite_id, args.question, evidence_text,
            lit_refs, records, reason=str(e)[:200], batch=batch,
            aldehyde=aldehyde, amine=amine)
        print(json.dumps({'written': written, 'count': len(written),
                          'batch': batch}, ensure_ascii=False))
        return

    # 6. 限量 + 去重 + 落盘
    items = items[:max_n]  # LLM 返回多条时只取前 max_n 条
    if not items:
        written = write_fallback_suggestion(
            sug_dir, args.favorite_id, args.question, evidence_text,
            lit_refs, records, reason='LLM 返回空数组', batch=batch,
            aldehyde=aldehyde, amine=amine)
    else:
        written = write_suggestions(sug_dir, items, args.favorite_id,
                                    records, lit_refs, rejected,
                                    batch=batch, max_n=max_n,
                                    aldehyde=aldehyde, amine=amine,
                                    graph_ref_ids=graph_ref_ids)
        if not written:  # 全部被去重
            written = write_fallback_suggestion(
                sug_dir, args.favorite_id, args.question, evidence_text,
                lit_refs, records, reason='建议均与已否决方向重复', batch=batch,
                aldehyde=aldehyde, amine=amine)

    # 7. stdout 打印一行 JSON 摘要
    print(json.dumps({'written': written, 'count': len(written),
                      'batch': batch}, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        # 兜底：任何未预期异常都走 stderr + 非 0 退出
        traceback.print_exc()
        sys.exit(1)
