"""Detect non-UTF-8 files in experiment/ and convert them to UTF-8."""
import os
from pathlib import Path

PROJ = Path(r'C:\Users\ckx\Desktop\minimax')
EXP = PROJ / 'experiment'

def detect_encoding(path):
    """Try UTF-8, then GBK, then Latin-1."""
    raw = path.read_bytes()
    # Check BOM
    if raw[:3] == b'\xef\xbb\xbf':
        return 'utf-8-sig', raw
    # Try UTF-8
    try:
        raw.decode('utf-8')
        return 'utf-8', raw
    except UnicodeDecodeError:
        pass
    # Try GBK
    try:
        raw.decode('gbk')
        return 'gbk', raw
    except UnicodeDecodeError:
        pass
    return 'latin-1', raw

converted = []
for f in EXP.rglob('*'):
    if f.is_file() and f.suffix in ('.md', '.csv', '.txt', '.json', '.yaml', '.py'):
        enc, raw = detect_encoding(f)
        if enc == 'gbk':
            text = raw.decode('gbk')
            f.write_text(text, encoding='utf-8')
            converted.append(f.name)
            print(f'  ✓ {f.name}: GBK → UTF-8')
        elif enc == 'utf-8':
            pass  # already good
        elif enc == 'latin-1':
            print(f'  ? {f.name}: Latin-1 (may need manual check)')

if not converted:
    print('No GBK files found. All are already UTF-8.')
else:
    print(f'\nConverted {len(converted)} files to UTF-8.')
