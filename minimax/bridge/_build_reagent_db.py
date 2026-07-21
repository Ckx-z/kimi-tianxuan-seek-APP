"""从 实验/方案/冰箱试剂.xlsx 转出为 JSON 试剂库。
执行一次即可，结果存到 experiment/reagent_db.json。
"""
import zipfile
import re
import json
import os

XLSX_PATH = r'C:\Users\ckx\Desktop\实验\方案\冰箱试剂.xlsx'
OUT_PATH = r'C:\Users\ckx\Desktop\minimax\experiment\reagent_db.json'
STRUCT_DIR = r'C:\Users\ckx\Desktop\minimax\experiment\structure'


def main():
    with zipfile.ZipFile(XLSX_PATH) as z:
        drawing_rels = z.read('xl/drawings/_rels/drawing1.xml.rels').decode('utf-8')
        sheet_xml = z.read('xl/worksheets/sheet1.xml').decode('utf-8')
        shared_strings = z.read('xl/sharedStrings.xml').decode('utf-8')

    rid_to_image = dict(re.findall(
        r'Id="(rId\d+)".*?Target="[^"]*image(\d+)\.png"', drawing_rels))
    rid_to_image = {k: f'image{v}.png' for k, v in rid_to_image.items()}

    ss_items = re.findall(r'<si>(.*?)</si>', shared_strings, re.DOTALL)
    ss_text = []
    for item in ss_items:
        parts = re.findall(r'<t[^>]*>([^<]*)</t>', item)
        ss_text.append(''.join(parts))

    rows_xml = re.findall(r'<row r="(\d+)"[^>]*>(.*?)</row>', sheet_xml, re.DOTALL)
    row_data = {}
    for rnum_str, rcontent in rows_xml:
        rnum = int(rnum_str)
        cells = re.findall(
            r'<c r="([A-Z]+\d+)"(?:[^>]*?t="s")?[^>]*?>(?:.*?<v>(\d+)</v>)?',
            rcontent)
        d = {}
        for cellref, val in cells:
            col_letters = re.match(r'([A-Z]+)\d+', cellref).group(1)
            if val and int(val) < len(ss_text):
                d[col_letters] = ss_text[int(val)]
            else:
                d[col_letters] = ''
        row_data[rnum] = d

    anchor_blocks = re.findall(
        r'<xdr:twoCellAnchor[^>]*>(.*?)</xdr:twoCellAnchor>', drawing_xml := '', re.DOTALL)
    # drawing_xml 实际是 read 出来的 drawing 内容, 重新读
    with zipfile.ZipFile(XLSX_PATH) as z:
        drawing_xml = z.read('xl/drawings/drawing1.xml').decode('utf-8')
    anchor_blocks = re.findall(
        r'<xdr:twoCellAnchor[^>]*>(.*?)</xdr:twoCellAnchor>', drawing_xml, re.DOTALL)

    cas_to_image = {}
    for blk in anchor_blocks:
        embed_m = re.search(r'r:embed="(rId\d+)"', blk)
        pos_m = re.search(
            r'<xdr:from>.*?<xdr:col>(\d+)</xdr:col>.*?<xdr:row>(\d+)</xdr:row>',
            blk, re.DOTALL)
        if embed_m and pos_m:
            rnum = int(pos_m.group(2)) + 1
            rd = row_data.get(rnum, {})
            cas = rd.get('C', '').strip()
            if cas:
                cas_to_image[cas] = rid_to_image.get(embed_m.group(1), '')

    reagents = []
    for rnum, rd in sorted(row_data.items()):
        if rnum == 1:
            continue  # 跳过表头
        cas = rd.get('C', '').strip()
        if not cas:
            continue
        image_file = cas_to_image.get(cas, '')
        rel_path = f'experiment/structure/{image_file}' if image_file else ''
        reagents.append({
            'cas': cas,
            'name_zh': rd.get('B', '').strip(),
            'name_short': rd.get('F', '').strip(),
            'mw': rd.get('D', '').strip(),
            'price': rd.get('E', '').strip(),
            'status': rd.get('G', '').strip(),  # 已买 / 未买
            'structure_image': rel_path,
        })

    # 节点类型推断（基于简称/名称）
    node_map = {
        'TAPT': 'C3_amine', 'TAPB': 'C3_amine', 'TFPT': 'C3_aldehyde', 'TFPB': 'C3_aldehyde',
        'TFPTA': 'C2_aldehyde_terphenyl', 'TFMB': 'C2_amine',
    }
    for r in reagents:
        ns = r.get('name_short', '')
        r['node_type'] = node_map.get(ns, 'C2_linear')

    out = {
        '_meta': {
            'source': '实验/方案/冰箱试剂.xlsx',
            'total': len(reagents),
            'note': '中心核节点: TAPT(苯→三嗪) / TAPB(苯) / TFPT(三嗪醛) / TFPB(苯醛)',
        },
        'reagents': reagents,
    }
    with open(OUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f'写入 {len(reagents)} 条试剂到 {OUT_PATH}')


if __name__ == '__main__':
    main()
