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


def retrieve_evidence(query_text: str, aldehyde: dict, amine: dict):
    """检索取证（降级链）。返回 (证据文本, literature_refs)

    - 优先 search_local_pdfs.search() + format_results_for_prompt()
    - GraphRAG 图检索 import/运行失败（如缺 networkx）时静默跳过
    """
    evidence_blocks = []
    lit_refs = []

    # 1. 本地 PDF / 反馈库 / embedding 检索
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
        gres = query_graphrag.query(query_text)
        lines = ['## GraphRAG 图检索']
        for h in (gres.get('reactions') or [])[:5]:
            d = h['data']
            lines.append(
                f"- [{h['score']}★] 醛 {d.get('aldehyde_name','?')} + 胺 {d.get('amine_name','?')} | "
                f"溶剂 {d.get('solvent','?')} | 温度 {d.get('temperature','?')} | "
                f"产物 {d.get('outcome','?')}")
        for h in (gres.get('literatures') or [])[:5]:
            d = h['data']
            lines.append(
                f"- [{h['score']}★] {h['id']} | {d.get('journal','?')} | "
                f"{str(d.get('innovation',''))[:120]}")
            lit_refs.append({'kind': 'literature', 'ref': str(h['id']),
                             'note': f"GraphRAG 文献节点 {d.get('journal','')}"})
        if len(lines) > 1:
            evidence_blocks.append('\n'.join(lines))
    except Exception:
        # 缺 networkx / graph.pkl 不存在等情况：静默跳过
        pass

    text = '\n\n'.join(b for b in evidence_blocks if b and b != '(无匹配)')
    return text or '(本次检索无匹配证据)', lit_refs


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

def build_messages(question, aldehyde, amine, records, evidence_text, rejected):
    """拼 prompt：证据文本 + 实验记录 + 严格 JSON 输出要求"""
    sys_prompt = (
        '你是 COF（共价有机框架）成膜实验的迭代顾问。'
        '基于用户的历史实验记录与检索到的文献证据，给出下一步可操作的实验建议。'
        '只输出一个严格 JSON 数组，不要输出任何其他文字、不要 markdown 代码块。'
        '数组元素格式: '
        '{"type": "condition_adjust|new_candidate|literature", '
        '"title": "一句话标题", '
        '"detail": "具体可操作的建议内容（条件调整需写明 字段/原值/改为/理由；'
        '新候选需写明醛胺 CAS 或名称及理由）", '
        '"evidence_refs": [{"kind": "experiment_record|literature|prediction", '
        '"ref": "rec_xxx 或文献名或 DOI", "note": "一句话说明"}]}'
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
    user_prompt = f"""## 用户问题
{question}

## 单体对
醛: {json.dumps(aldehyde or {}, ensure_ascii=False)}
胺: {json.dumps(amine or {}, ensure_ascii=False)}

## 相关实验记录
{records_text}

## 检索到的证据
{evidence_text}

## 已被用户否决的建议方向（不要重复）
{rejected_text}

请输出 1~3 条建议的 JSON 数组。"""
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


def normalize_evidence(item: dict, records, lit_refs):
    """规整 evidence_refs：只保留合法 kind，空的补检索证据"""
    refs = []
    for e in (item.get('evidence_refs') or []):
        if not isinstance(e, dict):
            continue
        kind = e.get('kind')
        if kind not in ('experiment_record', 'literature', 'prediction'):
            continue
        refs.append({'kind': kind, 'ref': str(e.get('ref', '')),
                     'note': str(e.get('note', ''))})
    if not refs:
        # 兜底：引用相关实验记录 + 检索文献
        refs = [{'kind': 'experiment_record', 'ref': r.get('record_id', ''),
                 'note': '相关历史实验记录'} for r in records[:2]]
        refs.extend(lit_refs[:2])
    return refs


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


def write_suggestions(sug_dir: Path, items, fav_id, records, lit_refs, rejected):
    """状态去重后按 Schema 3 落盘，返回写出的 suggestion_id 列表"""
    sug_dir.mkdir(parents=True, exist_ok=True)
    kept = []
    for item in items:
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
        doc = {
            'schema_version': SCHEMA_VERSION,
            'record_type': 'suggestion',
            'suggestion_id': sid,
            'favorite_id': fav_id,
            'type': t,
            'payload': payload,
            'evidence_refs': normalize_evidence(item, records, lit_refs),
            'created_at': now_iso(),
            'status': 'new',
        }
        (sug_dir / f'{sid}.json').write_text(
            json.dumps(doc, ensure_ascii=False, indent=1), encoding='utf-8')
        written.append(sid)
    return written


def write_fallback_suggestion(sug_dir: Path, fav_id, question, evidence_text,
                              lit_refs, records, reason):
    """LLM 失败/输出无法解析时的降级：写一条 type=literature 建议（只含检索证据）"""
    sug_dir.mkdir(parents=True, exist_ok=True)
    sid = next_sug_ids(sug_dir, 1)[0]
    refs = list(lit_refs[:5])
    refs.extend({'kind': 'experiment_record', 'ref': r.get('record_id', ''),
                 'note': '相关历史实验记录'} for r in records[:3])
    doc = {
        'schema_version': SCHEMA_VERSION,
        'record_type': 'suggestion',
        'suggestion_id': sid,
        'favorite_id': fav_id,
        'type': 'literature',
        'payload': {
            'title': f'LLM 暂不可用，以下为针对「{question[:30]}」检索到的原始证据',
            'detail': f'（降级建议，生成原因: {reason}）\n\n{evidence_text[:3000]}',
        },
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
    args = ap.parse_args()

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

    # 3. 检索取证（降级链）
    evidence_text, lit_refs = retrieve_evidence(query_text, aldehyde, amine)

    # 4. 状态去重：收集已否决方向
    rejected = load_rejected_directions(sug_dir, args.favorite_id)

    # 5. LLM 生成建议（失败则写降级建议）
    try:
        from llm_client import chat_completion
        messages = build_messages(args.question, aldehyde, amine, records,
                                  evidence_text, rejected)
        content, provider = chat_completion(messages, max_tokens=8000, timeout=120)
        print(f'[iterate_suggest] LLM 端点: {provider}', file=sys.stderr)
        items = parse_llm_json(content)
        if items is None:
            raise ValueError('LLM 输出无法解析为 JSON 数组')
    except Exception as e:
        print(f'[iterate_suggest] LLM 失败，写降级建议: {e}', file=sys.stderr)
        written = write_fallback_suggestion(
            sug_dir, args.favorite_id, args.question, evidence_text,
            lit_refs, records, reason=str(e)[:200])
        print(json.dumps({'written': written, 'count': len(written)},
                         ensure_ascii=False))
        return

    # 6. 去重 + 落盘
    if not items:
        written = write_fallback_suggestion(
            sug_dir, args.favorite_id, args.question, evidence_text,
            lit_refs, records, reason='LLM 返回空数组')
    else:
        written = write_suggestions(sug_dir, items, args.favorite_id,
                                    records, lit_refs, rejected)
        if not written:  # 全部被去重
            written = write_fallback_suggestion(
                sug_dir, args.favorite_id, args.question, evidence_text,
                lit_refs, records, reason='建议均与已否决方向重复')

    # 7. stdout 打印一行 JSON 摘要
    print(json.dumps({'written': written, 'count': len(written)},
                     ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        # 兜底：任何未预期异常都走 stderr + 非 0 退出
        traceback.print_exc()
        sys.exit(1)
