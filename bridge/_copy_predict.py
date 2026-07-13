"""从 tianxuan-seek 复制最小运行集到 minimax/predict/"""
import shutil
import os

SRC = r'C:\Users\ckx\Desktop\tianxuan seek'
DST = r'C:\Users\ckx\Desktop\minimax\predict'

def copy_file(rel_path):
    src = os.path.join(SRC, rel_path)
    dst = os.path.join(DST, rel_path)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.exists(src):
        shutil.copy2(src, dst)
        print(f'  copy: {rel_path}')
    else:
        print(f'  MISS: {rel_path}')

def copy_dir(rel_dir, exclude=()):
    src_dir = os.path.join(SRC, rel_dir)
    dst_dir = os.path.join(DST, rel_dir)
    if not os.path.isdir(src_dir):
        print(f'  DIR MISS: {rel_dir}')
        return
    for root, dirs, files in os.walk(src_dir):
        # filter exclusion
        dirs[:] = [d for d in dirs if d not in exclude and not d.startswith('__pycache__')]
        for f in files:
            if f.endswith('.pyc') or f.startswith('.'):
                continue
            src = os.path.join(root, f)
            rel = os.path.relpath(src, SRC)
            dst = os.path.join(DST, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
    print(f'  copy dir: {rel_dir}/ (skipped __pycache__)')

print('=== 复制最小可运行集 ===')
# 1. 顶层入口
copy_file('predict_pair.py')

# 2. 完整 GNN 模型包 (v4) + featurizer (v3)
copy_dir('src/screening/gnn_v4')
copy_dir('src/screening/gnn_v3', exclude=('featurizer_only',))
# featurizer.py 必须存在，但 v3 目录下其他文件也要
copy_file('src/screening/__init__.py')

# 3. 完整 chemistry 包（hard_rules 依赖项）
copy_dir('src/chemistry')

# 4. utils 包
copy_dir('src/utils')

# 5. scripts/screen_v4.py (被 inference 调)
copy_file('scripts/screen_v4.py')

# 6. config + 模型 + 数据 + 依赖
copy_file('config/model_v4.yaml')
copy_file('data/processed/merged_monomer_pool.csv')
copy_file('requirements.txt')

# 7. 模型权重（最新 v5.3）
copy_file('models/v5.3/v5_model.pt')

print('\n=== 验证 ===')
for f in ['predict_pair.py', 'src/screening/gnn_v4/model.py', 'src/chemistry/hard_rules.py',
          'src/screening/gnn_v3/featurizer.py', 'scripts/screen_v4.py',
          'config/model_v4.yaml', 'data/processed/merged_monomer_pool.csv',
          'models/v5.3/v5_model.pt', 'requirements.txt']:
    full = os.path.join(DST, f)
    if os.path.exists(full):
        size = os.path.getsize(full)
        print(f'  OK {f}  ({size:,} bytes)')
    else:
        print(f'  MISSING {f}')
