"""
adapters/cof_app_ingest.py
==========================
COF App (全新机器学习实验) rag_export 摄入适配器

读取 App 侧 data/rag_export/ 下的 predictions/ + records/，
按 schema_version 1.0 校验，转成 minimax 内部可消费格式:

  - records     -> feedback_db.csv 同表头行 (中文表头), 人工核对后追加
  - predictions -> 统一先验 JSONL (供检索层扩展消费)

安全原则:
  - 默认干跑 (dry-run), 只校验 + 打印摘要, 不写任何文件
  - --apply 也只写 bridge/cof_app_import/<带日期新文件>, 绝不覆盖现有文件
  - 绝不直写 experiment/feedback_db.csv

用法:
  python adapters/cof_app_ingest.py                # 干跑
  python adapters/cof_app_ingest.py --apply        # 写出转换结果
  python adapters/cof_app_ingest.py --export-dir D:\\path\\to\\rag_export

契约文档: docs/COF_APP_CONTRACT.md (schema 权威定义在 App 侧 rag_export/README.md)
"""
import argparse
import csv
import io
import json
import os
import sys
import datetime
from pathlib import Path

# ---- 路径配置 ----
HERE = Path(__file__).parent.resolve()
PROJ = HERE.parent
DEFAULT_EXPORT_DIR = Path(r'C:\Users\ckx\Desktop\全新机器学习实验') / 'data' / 'rag_export'
IMPORT_DIR = PROJ / 'bridge' / 'cof_app_import'

SUPPORTED_SCHEMA = '1.0'

# feedback_db.csv 表头 (与 experiment/feedback_db.csv 完全一致, 顺序不得变)
FEEDBACK_HEADER = [
    '方案编号', '日期', '版本', '醛CAS', '醛SMILES', '醛名称', '醛结构式路径',
    '胺CAS', '胺SMILES', '胺名称', '胺结构式路径',
    'tianxuan_预测概率', 'tianxuan_MC标准差', '试剂状态', '阳性对照', '单一变量',
    '科学问题', '失败Class', '失败现象描述', '根因Type', '根因Notes',
    'PXRD文件', 'FTIR文件', 'SEM文件', '关联历史失败', '关联外推依据', '下一轮建议', '备注',
]

# outcome -> 失败Class 兜底映射 (优先用 record.failure_class)
OUTCOME_TO_CLASS = {'film': 'F', 'partial': 'E', 'failed': 'C'}

REQUIRED_PRED = ['prediction_id', 'aldehyde', 'amine', 'score', 'score_std',
                 'arm', 'ood', 'model_version', 'timestamp']
REQUIRED_REC = ['record_id', 'aldehyde', 'amine', 'conditions',
                'outcome', 'date']


def load_json_dir(d: Path):
    """读取目录下全部 .json, 返回 [(path, dict|None, error|None)]"""
    out = []
    if not d.exists():
        return out
    for f in sorted(d.glob('*.json')):
        try:
            out.append((f, json.loads(f.read_text(encoding='utf-8')), None))
        except Exception as e:
            out.append((f, None, str(e)))
    return out


def validate(obj: dict, required: list, kind: str, path: Path):
    """校验 schema_version + 必填字段, 返回错误信息列表 (空=通过)"""
    errs = []
    ver = obj.get('schema_version')
    if ver != SUPPORTED_SCHEMA:
        errs.append(f'{path.name}: schema_version={ver!r} 不支持 (当前支持 {SUPPORTED_SCHEMA}), 跳过')
        return errs
    if obj.get('record_type') != kind:
        errs.append(f'{path.name}: record_type={obj.get("record_type")!r} != {kind!r}')
    for k in required:
        if k not in obj:
            errs.append(f'{path.name}: 缺必填字段 {k}')
    # OOD out 时 score 必须为 null (契约硬性约定)
    if kind == 'prediction':
        ood = obj.get('ood') or {}
        if ood.get('level') == 'out' and obj.get('score') is not None:
            errs.append(f'{path.name}: ood=out 但 score 非 null, 违反契约')
    if kind == 'experiment_record':
        if obj.get('outcome') not in OUTCOME_TO_CLASS:
            errs.append(f'{path.name}: outcome={obj.get("outcome")!r} 非法 (film/partial/failed)')
    return errs


def monomer(m: dict):
    """单体对象 -> (cas, smiles, name)"""
    m = m or {}
    return m.get('cas', ''), m.get('smiles', ''), m.get('name', '')


def record_to_feedback_row(rec: dict) -> dict:
    """experiment_record -> feedback_db.csv 行 (dict)

    映射决策 (见 docs/COF_APP_CONTRACT.md):
    - App score 是树模型"倾向性", 语义不同于 tianxuan GNN 概率,
      不写 tianxuan_预测概率 列, 放 备注
    - conditions 无独立列 -> 并入 备注
    - 失败Class 优先 record.failure_class, 缺失时按 outcome 兜底
    """
    ald_cas, ald_smi, ald_name = monomer(rec.get('aldehyde'))
    am_cas, am_smi, am_name = monomer(rec.get('amine'))

    snap = rec.get('prediction_snapshot') or {}
    score_note = ''
    if snap.get('score') is not None:
        score_note = f'app_score={snap["score"]:.3f}±{snap.get("std", 0):.3f}(ood={snap.get("ood", "?")})'

    cond = rec.get('conditions') or {}
    cond_note = '条件: ' + '; '.join(
        f'{k}={v}' for k, v in cond.items() if v not in (None, ''))

    failure_class = rec.get('failure_class') or OUTCOME_TO_CLASS.get(rec.get('outcome'), '')
    phenomenon = rec.get('strength') or ''
    if rec.get('notes'):
        phenomenon = f'{phenomenon}; {rec["notes"]}' if phenomenon else rec['notes']

    # 方案编号: 优先回链 minimax 编号, 否则用 app record_id (独立体系, 不混)
    plan_no = rec.get('minimax_plan_no') or f'APP-{rec.get("record_id", "")}'

    return {
        '方案编号': plan_no,
        '日期': rec.get('date', ''),
        '版本': '1',
        '醛CAS': ald_cas, '醛SMILES': ald_smi, '醛名称': ald_name, '醛结构式路径': '',
        '胺CAS': am_cas, '胺SMILES': am_smi, '胺名称': am_name, '胺结构式路径': '',
        'tianxuan_预测概率': '',   # 语义不同, 故意留空
        'tianxuan_MC标准差': '',
        '试剂状态': '', '阳性对照': '', '单一变量': '', '科学问题': '',
        '失败Class': failure_class,
        '失败现象描述': phenomenon,
        '根因Type': '', '根因Notes': '',
        'PXRD文件': '', 'FTIR文件': '', 'SEM文件': '',
        '关联历史失败': rec.get('favorite_id') or '',
        '关联外推依据': rec.get('prediction_id') or '',
        '下一轮建议': '',
        '备注': '; '.join(x for x in [score_note, cond_note] if x),
    }


def prediction_to_prior(pred: dict) -> dict:
    """prediction -> 统一先验记录 (一行一条 JSONL)"""
    ald_cas, ald_smi, ald_name = monomer(pred.get('aldehyde'))
    am_cas, am_smi, am_name = monomer(pred.get('amine'))
    return {
        'prediction_id': pred.get('prediction_id'),
        'ald': {'cas': ald_cas, 'smiles': ald_smi, 'name': ald_name},
        'amine': {'cas': am_cas, 'smiles': am_smi, 'name': am_name},
        'app_score': pred.get('score'),
        'app_score_std': pred.get('score_std'),
        'arm': pred.get('arm'),
        'ood_level': (pred.get('ood') or {}).get('level'),
        'model_version': pred.get('model_version'),
        'timestamp': pred.get('timestamp'),
        'source': 'cof_app',
    }


def main():
    ap = argparse.ArgumentParser(description='COF App rag_export 摄入适配器')
    ap.add_argument('--apply', action='store_true',
                    help='实际写出到 bridge/cof_app_import/ (默认干跑)')
    ap.add_argument('--export-dir', type=Path,
                    default=Path(os.environ.get('COF_APP_RAG_EXPORT', DEFAULT_EXPORT_DIR)),
                    help='rag_export 根目录 (默认: App 侧 data/rag_export)')
    args = ap.parse_args()

    export_dir: Path = args.export_dir
    print(f'=== COF App rag_export 摄入 ({"APPLY" if args.apply else "DRY-RUN"}) ===')
    print(f'来源: {export_dir}')
    if not export_dir.exists():
        print(f'!! 目录不存在, 无数据可摄入 (App 侧尚未导出?)')
        return 0

    errors, preds, recs = [], [], []

    for path, obj, err in load_json_dir(export_dir / 'predictions'):
        if err:
            errors.append(f'{path.name}: JSON 解析失败 {err}')
            continue
        if path.stem == 'example':
            continue  # 契约示例文件, 不摄入
        errs = validate(obj, REQUIRED_PRED, 'prediction', path)
        if errs:
            errors.extend(errs)
        else:
            preds.append(obj)

    for path, obj, err in load_json_dir(export_dir / 'records'):
        if err:
            errors.append(f'{path.name}: JSON 解析失败 {err}')
            continue
        if path.stem == 'example':
            continue
        errs = validate(obj, REQUIRED_REC, 'experiment_record', path)
        if errs:
            errors.extend(errs)
        else:
            recs.append(obj)

    # suggestions/ 是 minimax 产出方向, 本适配器只读状态供参考
    sug_status = {}
    for path, obj, err in load_json_dir(export_dir / 'suggestions'):
        if obj and path.stem != 'example':
            sug_status[obj.get('suggestion_id', path.stem)] = obj.get('status', '?')

    print(f'\npredictions: {len(preds)} 条有效')
    print(f'records:     {len(recs)} 条有效')
    print(f'suggestions: {len(sug_status)} 条 (状态: {dict(sorted(sug_status.items())) if sug_status else "无"})')
    ood_out = [p["prediction_id"] for p in preds if (p.get("ood") or {}).get("level") == "out"]
    if ood_out:
        print(f'  ⚠ ood=out 的预测 {len(ood_out)} 条, 打分不可信, 禁止当先验: {ood_out}')

    if errors:
        print(f'\n!! 校验问题 {len(errors)} 条:')
        for e in errors:
            print(f'  - {e}')

    if not args.apply:
        print('\n(dry-run, 未写任何文件; 加 --apply 写出转换结果)')
        return 1 if errors else 0

    IMPORT_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.date.today().isoformat()

    if recs:
        csv_path = IMPORT_DIR / f'feedback_rows_{today}.csv'
        if csv_path.exists():
            print(f'!! {csv_path.name} 已存在, 不覆盖, 跳过 (同日重复运行请先核对旧文件)')
        else:
            with open(csv_path, 'w', encoding='utf-8-sig', newline='') as f:
                w = csv.DictWriter(f, fieldnames=FEEDBACK_HEADER)
                w.writeheader()
                for rec in recs:
                    w.writerow(record_to_feedback_row(rec))
            print(f'\n✓ 写出 {csv_path} ({len(recs)} 行)')
            print('  人工核对后再追加进 experiment/feedback_db.csv')

    if preds:
        jsonl_path = IMPORT_DIR / f'predictions_{today}.jsonl'
        if jsonl_path.exists():
            print(f'!! {jsonl_path.name} 已存在, 不覆盖, 跳过')
        else:
            with open(jsonl_path, 'w', encoding='utf-8') as f:
                for p in preds:
                    f.write(json.dumps(prediction_to_prior(p), ensure_ascii=False) + '\n')
            print(f'✓ 写出 {jsonl_path} ({len(preds)} 行)')

    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
