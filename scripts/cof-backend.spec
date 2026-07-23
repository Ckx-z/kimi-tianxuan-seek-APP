# -*- mode: python ; coding: utf-8 -*-
"""COF 后端 PyInstaller spec（onedir）。

构建（项目根）：
    "E:/ANACONDA/python.exe" -m PyInstaller scripts/cof-backend.spec --noconfirm --clean
产物：
    dist-backend/cof-backend/cof-backend.exe（+ _internal/ 资源）

打包内容：FastAPI 后端 + src/ 预测栈 + minimax GraphRAG 链路（编排器与
bridge 以真实 .py 文件形式作为 datas 携带，供主进程内 importlib 执行）。
用户数据不落包：frozen 时写 %APPDATA%/COF-Film-Recommend（见
src/runtime_config.py 的 user_app_root/user_data_root）。
"""

from pathlib import Path

ROOT = Path(SPECPATH).parent  # spec 在 scripts/ 下，项目根为其父目录


def _files(pattern: str, dest: str):
    """按 glob 收集文件 → (src, dest_dir) 列表（仅文件，跳过 __pycache__）。"""
    out = []
    for p in sorted(ROOT.glob(pattern)):
        if p.is_file() and "__pycache__" not in p.parts:
            out.append((str(p), dest))
    return out


datas = [
    (r"E:/ANACONDA/Lib/site-packages/xgboost/VERSION", "xgboost"),
    # 模型资产（树模型路由 + OOD 包络 + 单体池）
    (str(ROOT / "models"), "models"),
    # 前端静态产物（SPA，挂载 /）
    (str(ROOT / "webapp" / "dist"), "webapp/dist"),
    # 图标与运行时配置模板
    (str(ROOT / "assets" / "app_icon.ico"), "assets"),
    (str(ROOT / "config" / "runtime.example.json"), "config"),
    # 只读数据资产
    (str(ROOT / "data" / "builtin_monomers.json"), "data"),
    (str(ROOT / "data" / "paper_titles.json"), "data"),
    (str(ROOT / "data" / "experimental_refs"), "data/experimental_refs"),
    (str(ROOT / "data" / "plan_templates"), "data/plan_templates"),
    (str(ROOT / "data" / "interim" / "v5_train_stage1_cond_filled.csv"),
     "data/interim"),
    # minimax GraphRAG 链路：编排器 + bridge 模块（真实 .py，importlib 加载）
    (str(ROOT / "minimax" / "experiment"), "minimax/experiment"),
    (str(ROOT / "minimax" / "bridge" / "knowledge_index.jsonl"),
     "minimax/bridge"),
]
datas += _files("minimax/adapters/*.py", "minimax/adapters")
datas += _files("minimax/bridge/*.py", "minimax/bridge")
datas += _files("minimax/bridge/graphrag_v2/*.py", "minimax/bridge/graphrag_v2")
# GraphRAG 图资产（graph.pkl / graph_v2.pkl / 文献 embedding）
for name in ("graph.pkl", "graph_v2.pkl", "lit_embeddings.jsonl",
             "meta.json", "embedding_meta.json"):
    p = ROOT / "minimax" / "bridge" / "graphrag" / name
    if p.is_file():
        datas.append((str(p), "minimax/bridge/graphrag"))

hiddenimports = [
    # uvicorn 运行时按字符串惰性加载的实现
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.wsproto_impl",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    # FastAPI 应用本体（uvicorn.run("api.main:app") 为字符串引用，
    # 分析器看不到，显式指定后其静态 import 会级联打包 src 全栈）
    "api.main",
]

a = Analysis(
    [str(ROOT / "scripts" / "backend_entry.py")],
    pathex=[str(ROOT), str(ROOT / "src"), str(ROOT / "src" / "features")],
    binaries=[
        # anaconda 的 scipy.special._ufuncs 链接 netlib lapack/blas 包装库，
        # PyInstaller 依赖扫描漏收，显式带上（否则 DLL load failed）
        (r"E:/ANACONDA/Library/bin/liblapack.dll", "."),
        (r"E:/ANACONDA/Library/bin/libblas.dll", "."),
        (r"E:/ANACONDA/Library/bin/libcblas.dll", "."),
        # XGBoost 原生库（hook 未收集，运行时按包内 lib/ 布局查找）
        (r"E:/ANACONDA/Lib/site-packages/xgboost/lib/xgboost.dll", "xgboost/lib"),
    ],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 大包裁剪：GNN/训练/桌面 App 链路不进后端包
        "torch", "torch_geometric", "torchvision", "torchaudio",
        "gradio", "shap", "matplotlib", "seaborn", "plotly",
        "tensorboard", "pytest", "ruff",
        # Qt/Jupyter 链路（主环境装有，运行时不需要）
        "PyQt5", "PyQt6", "PySide2", "PySide6", "tkinter",
        "IPython", "ipykernel", "jupyter", "notebook", "zmq",
        "black", "yapf",
        # 可选依赖误拉（pandas/fsspec 等的 try-import 分支）
        "panel", "bokeh", "botocore", "boto3", "s3fs", "gcsfs",
        "numba", "llvmlite", "pyarrow", "skimage", "scikit-image",
        "sphinx", "alabaster", "altair", "aiohttp", "babel",
        "dask", "distributed", "numexpr", "tables",
        "cv2", "opencv_python", "cytoscape", "dash",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

# MKL 运行库裁剪：intel_thread + def/mc/mc3/avx2 已覆盖新老 CPU 的分发，
# 去掉 pgi/tbb/sequential 线程层与 avx/avx512 专用变体（MKL 会自动回退）
_DROP_DLL_MARKERS = (
    "mkl_pgi_thread", "mkl_tbb_thread", "mkl_sequential",
    "mkl_avx.", "mkl_avx512", "mkl_vml_avx.", "mkl_vml_avx512",
)
a.binaries = [b for b in a.binaries
              if not any(m in b[0] for m in _DROP_DLL_MARKERS)]

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="cof-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    icon=str(ROOT / "assets" / "app_icon.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="cof-backend",
)
