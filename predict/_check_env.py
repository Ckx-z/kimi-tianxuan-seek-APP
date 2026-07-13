"""predict/ 环境检查脚本
返回 conda env dphuanjing 是否就绪。
"""
import sys
import importlib

def check():
    print('Python:', sys.version)
    print('路径:', sys.executable)
    print()

    deps = ['torch', 'torch_geometric', 'rdkit', 'numpy', 'pandas',
            'yaml', 'docx', 'sklearn']

    results = {}
    for d in deps:
        try:
            m = importlib.import_module(d if d != 'yaml' else 'yaml')
            ver = getattr(m, '__version__', '?')
            results[d] = ('OK', ver)
        except ImportError as e:
            results[d] = ('MISS', str(e)[:50])

    print('=== 依赖检查 ===')
    for d, (status, info) in results.items():
        marker = '✓' if status == 'OK' else '✗'
        print(f'  {marker} {d:20s} {info}')
    return all(s == 'OK' for s, _ in results.values())

if __name__ == '__main__':
    if not check():
        print()
        print('需要安装依赖 (在 conda env dphuanjing 中运行):')
        print('   pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121')
        print('   pip install torch_geometric')
        print('   pip install rdkit scikit-learn numpy pandas pyyaml python-docx')
        sys.exit(1)
