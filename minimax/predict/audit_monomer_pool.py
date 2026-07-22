"""
predict/audit_monomer_pool.py
=============================
monomer_pool 清洗脚本 (波次2 任务D-②)

用 RDKit 按 SMILES 重算醛基/氨基官能团数, 与 n_aldehyde / n_amine /
monomer_type / is_aldehyde / is_amine 对账:

- RDKit 能确证的错误 → 直接修正 (官能团计数 / is_* / 明确的 type 矛盾)
- 存疑行 (双官能团/无官能团/RDKit 解析失败/类型无法确证) → audit_flag=review, 保留
- 明显错误名 (同位素标记 15N/13C/氘代、残留标注) → 剔除

输出:
- 清洗后: predict/data/processed/merged_monomer_pool.csv (原地, 已先备份 .bak_20260722)
- 审计报告: reports/monomer_pool_audit.csv

运行 (RDKit 环境):
    E:\\ANACONDA\\python.exe predict/audit_monomer_pool.py
"""
import csv
import re
import sys
from pathlib import Path

from rdkit import Chem
from rdkit import RDLogger

RDLogger.DisableLog('rdApp.*')  # 静默 RDKit 解析警告

ROOT = Path(__file__).resolve().parent.parent
CSV_FP = ROOT / 'predict' / 'data' / 'processed' / 'merged_monomer_pool.csv'
REPORT_DIR = ROOT / 'reports'
REPORT_FP = REPORT_DIR / 'monomer_pool_audit.csv'

# 醛基: 脂肪/芳香醛 -CHO (羰基碳带 1 个 H, 排除酮/酯/酰胺羰基)
ALDEHYDE_SMARTS = '[#6;H1](=O)'
# 氨基: 连接在碳上的伯胺 -NH2 / 仲胺 -NH- (排除酰胺 N、硝基 N、芳环 N)
AMINE_SMARTS = '[NX3;H2,H1;!$(NC=O);!$(N[N,O,S])][#6]'

# 同位素/残留标注 (明显错误名)
# 名称用文本模式 (15N / ¹⁵N / 氘代 等); SMILES 只认括号同位素记号 ([15N]/[13C]/[2H]),
# 避免环闭合数字 (%13c) 误伤
ISOTOPE_NAME_PAT = re.compile(
    r'(¹⁵N|¹³C|¹⁴N|²H|15N|13C|14N[-)]|氘代|deuterat|isotop|'
    r'标记残留|残留标注|\[15N|\[13C)', re.IGNORECASE)
ISOTOPE_SMILES_PAT = re.compile(r'\[(?:1[345][A-Za-z]|2H)\d*\]')

ALDEHYDE_PATT = Chem.MolFromSmarts(ALDEHYDE_SMARTS)
AMINE_PATT = Chem.MolFromSmarts(AMINE_SMARTS)


def count_groups(mol):
    """RDKit 重算 (n_aldehyde, n_amine); mol 为 None 时返回 None"""
    if mol is None:
        return None
    n_ald = len(mol.GetSubstructMatches(ALDEHYDE_PATT))
    n_am = len(mol.GetSubstructMatches(AMINE_PATT))
    return n_ald, n_am


def derive_type(n_ald, n_am):
    """由官能团数推导类型: aldehyde / amine / both / none"""
    if n_ald > 0 and n_am > 0:
        return 'both'
    if n_ald > 0:
        return 'aldehyde'
    if n_am > 0:
        return 'amine'
    return 'none'


def main():
    rows = []
    with open(CSV_FP, encoding='utf-8', newline='') as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames)
        for row in reader:
            rows.append(row)

    if 'audit_flag' not in fieldnames:
        fieldnames.append('audit_flag')

    audit_rows = []
    kept = []
    n_fixed = 0      # RDKit 确证后直接修正的行数
    n_dropped = 0    # 剔除的行数 (同位素/残留标注)
    n_review = 0     # 标记 review 保留的行数

    for i, row in enumerate(rows):
        line_no = i + 2  # csv 行号 (含表头)
        smi = (row.get('smiles') or '').strip()
        name = (row.get('best_name') or '').strip()
        issues = []       # 审计发现的问题描述
        action = 'keep'   # keep / fixed / dropped / review

        # 1. 明显错误名: 同位素标记 / 残留标注 (名称或 SMILES) → 剔除
        if ISOTOPE_NAME_PAT.search(name) or ISOTOPE_SMILES_PAT.search(smi):
            issues.append('同位素/残留标注')
            action = 'dropped'

        # 2. RDKit 解析
        mol = Chem.MolFromSmiles(smi) if smi else None
        if action != 'dropped':
            counts = count_groups(mol)
            if counts is None:
                issues.append('RDKit 解析失败')
                action = 'review'
            else:
                n_ald, n_am = counts
                old_ald = int(row.get('n_aldehyde') or 0)
                old_am = int(row.get('n_amine') or 0)
                old_type = (row.get('monomer_type') or '').strip().lower()
                old_is_ald = (row.get('is_aldehyde') or '').strip() == 'True'
                old_is_am = (row.get('is_amine') or '').strip() == 'True'
                new_type = derive_type(n_ald, n_am)

                # 计数不一致 → RDKit 确证, 直接修正
                if n_ald != old_ald or n_am != old_am:
                    issues.append(f'计数修正 n_aldehyde {old_ald}→{n_ald}, n_amine {old_am}→{n_am}')
                    row['n_aldehyde'] = str(n_ald)
                    row['n_amine'] = str(n_am)
                    action = 'fixed'

                # is_aldehyde / is_amine 与重算矛盾 → 直接修正
                if old_is_ald != (n_ald > 0):
                    issues.append(f'is_aldehyde {old_is_ald}→{n_ald > 0}')
                    row['is_aldehyde'] = str(n_ald > 0)
                    action = 'fixed'
                if old_is_am != (n_am > 0):
                    issues.append(f'is_amine {old_is_am}→{n_am > 0}')
                    row['is_amine'] = str(n_am > 0)
                    action = 'fixed'

                # monomer_type 与重算类型矛盾
                if old_type and old_type != new_type:
                    # 能确证: 新旧都是 aldehyde/amine 单一类型 (如 type=aldehyde 但实为纯胺)
                    if old_type in ('aldehyde', 'amine') and new_type in ('aldehyde', 'amine'):
                        issues.append(f'type 矛盾 {old_type}→{new_type} (RDKit 确证)')
                        row['monomer_type'] = new_type
                        action = 'fixed'
                    else:
                        # 双官能团/无官能团/未知类型 → 存疑保留
                        issues.append(f'type 存疑 {old_type} vs RDKit={new_type}')
                        if action != 'fixed':
                            action = 'review'

        if action == 'dropped':
            n_dropped += 1
        else:
            if action == 'review':
                row['audit_flag'] = 'review'
                n_review += 1
            elif action == 'fixed':
                row['audit_flag'] = 'fixed'
                n_fixed += 1
            else:
                row['audit_flag'] = ''
            kept.append(row)

        if issues:
            audit_rows.append({
                'csv_line': line_no,
                'smiles': smi,
                'best_name': name,
                'action': action,
                'issues': '; '.join(issues),
            })

    # 写审计报告
    REPORT_DIR.mkdir(exist_ok=True)
    with open(REPORT_FP, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['csv_line', 'smiles', 'best_name', 'action', 'issues'])
        w.writeheader()
        w.writerows(audit_rows)

    # 写清洗后 csv (原地覆盖, 调用方已先备份)
    with open(CSV_FP, 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(kept)

    print(f'原始行数: {len(rows)}')
    print(f'修正 (fixed): {n_fixed}')
    print(f'剔除 (dropped): {n_dropped}')
    print(f'标记存疑 (review): {n_review}')
    print(f'保留总行数: {len(kept)}')
    print(f'审计报告: {REPORT_FP} (共 {len(audit_rows)} 条问题记录)')


if __name__ == '__main__':
    main()
