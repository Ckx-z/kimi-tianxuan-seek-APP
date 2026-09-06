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


def _tree(subdir: str, dest: str):
    """按目录树收集 .py 文件并保持相对子路径（gnn_runtime 推理运行时用）。

    gnn_runtime/predict_pair.py 按「脚本自身目录 + src/ 子包」布局 import，
    打包时必须保持 src/screening/... 的目录结构，不能拍平。
    """
    out = []
    base = ROOT / subdir
    if base.is_dir():
        for p in sorted(base.rglob("*.py")):
            if "__pycache__" in p.parts:
                continue
            rel = p.relative_to(base).parent.as_posix()
            out.append((str(p), dest if rel == "." else f"{dest}/{rel}"))
    return out


datas = [
    (r"E:/ANACONDA/Lib/site-packages/xgboost/VERSION", "xgboost"),
    # 模型资产（树模型路由 + OOD 包络 + 单体池 + GNN v5.4 权重与校准器
    # models/gnn_v5.4/v5_model.pt、calibrator.pkl 随整目录一并打包）
    (str(ROOT / "models"), "models"),
    # GNN v5.4 推理运行时（随包分发：predict_pair.py + src 子包；
    # torch/PyG 解释器 dphuanjing 不随包——缺失时 GNN 分量优雅降级）
    *[x for x in _tree("gnn_runtime", "gnn_runtime")],
    # 前端静态产物（SPA，挂载 /）
    (str(ROOT / "webapp" / "dist"), "webapp/dist"),
    # 图标与运行时配置模板
    (str(ROOT / "assets" / "app_icon.ico"), "assets"),
    (str(ROOT / "config" / "runtime.example.json"), "config"),
    # 只读数据资产
    (str(ROOT / "data" / "builtin_monomers.json"), "data"),
    (str(ROOT / "data" / "paper_titles.json"), "data"),
    # GNN 验证闸门（v1.8.0）：金标准集 + 基线快照随包（frozen 下 guard 可用）
    (str(ROOT / "data" / "film_gold_standard.json"), "data"),
    (str(ROOT / "data" / "film_gold_baseline.json"), "data"),
    (str(ROOT / "data" / "experimental_refs"), "data/experimental_refs"),
    (str(ROOT / "data" / "plan_templates"), "data/plan_templates"),
    (str(ROOT / "data" / "interim" / "v5_train_stage1_cond_filled.csv"),
     "data/interim"),
    # 组合级训练池（pair_pool）：v6 与 tree_v5 实际训练集同口径
    (str(ROOT / "data" / "interim" / "v6_train_stage1.csv"),
     "data/interim"),
    # minimax GraphRAG 链路：编排器 + bridge 模块（真实 .py，importlib 加载）
    (str(ROOT / "minimax" / "experiment"), "minimax/experiment"),
    (str(ROOT / "minimax" / "bridge" / "knowledge_index.jsonl"),
     "minimax/bridge"),
    # 科研助手人格文件（ming 身份卡 + 领域规则，数据文件）
    (str(ROOT / "src" / "assistant" / "persona"), "src/assistant/persona"),
]
datas += _files("minimax/adapters/*.py", "minimax/adapters")
datas += _files("minimax/bridge/*.py", "minimax/bridge")
datas += _files("minimax/bridge/graphrag_v2/*.py", "minimax/bridge/graphrag_v2")
# GNN 反馈微调编排（v1.8.0）：finetune/run_job/guard_eval/compare_versions
# 为真实 .py 随包携带（frozen 下由 ANACONDA/dphuanjing 解释器执行）
datas += _files("gnn_training/*.py", "gnn_training")
# xTB 计算引擎（DFT 模块）：目录存在才打包；frozen 下引擎路径解析到
# _MEIPASS/vendor/xtb（见 src/dft/engine.py 的 xtb_binary/xtb_share_dir）
if (ROOT / "vendor" / "xtb" / "bin" / "xtb.exe").is_file():
    datas.append((str(ROOT / "vendor" / "xtb"), "vendor/xtb"))
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
    # frozen 下 sys.path[0]=_MEIPASS/src（api/deps.py 插入），其 finder 前缀为
    # "src"，惰性导入的 recommend.* 子模块会被解析为 src.recommend.*；
    # 缺哪个就在 frozen 下 ModuleNotFoundError（源码模式无此问题）。
    # 此处显式补齐 src.* 命名空间变体。
    "recommend.monomer_props",
    "src.recommend.monomer_props",
    # DFT 模块：路由静态链可及 src.dft.*，但 jobs 内惰性 import
    # favorites.store（双序查配对）与 dft.* 顶层命名空间变体一并补齐
    "dft.engine", "src.dft.engine",
    "dft.jobs", "src.dft.jobs",
    "dft.cache", "src.dft.cache",
    "dft.log", "src.dft.log",
    "favorites.store", "src.favorites.store",
    # 文献录入链路（routers/literature.py 函数内惰性 import，静态分析漏收；
    # 双命名空间变体同 recommend/dft 口径）
    "literature.resolver", "src.literature.resolver",
    "literature.crossref", "src.literature.crossref",
    "literature.pdf_extract", "src.literature.pdf_extract",
    # 文献图谱（v1.7.0）：literature.figures 同为路由内惰性 import
    "literature.figures", "src.literature.figures",
    # 实验记录导出（routers/records.py 内惰性 import；docx → lxml 原生依赖
    # 见下方 binaries）
    "records.store", "src.records.store",
    "records.export_docx", "src.records.export_docx",
    # LLM 门面与方案模板（routers/llm.py、routers/plan.py 内惰性 import）
    "llm.client", "src.llm.client",
    "recommend.plan_templates", "src.recommend.plan_templates",
    "recommend.plan_card", "src.recommend.plan_card",
    "runtime_config", "src.runtime_config",
    # GNN 反馈/重训（v1.8.0：routers/gnn_feedback.py 内惰性 import）
    "predictor.gnn_feedback", "src.predictor.gnn_feedback",
    "predictor.gnn_jobs", "src.predictor.gnn_jobs",
    # PDF 提取（PyMuPDF，原生组件）与 multipart 上传解析
    "fitz", "pymupdf",
    "multipart",
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
        # lxml（python-docx 依赖的 lxml.etree）原生依赖：anaconda 的 lxml .pyd
        # 动态链接 Library/bin 下这些 DLL，PyInstaller 依赖扫描漏收，显式带上
        # （否则 frozen 下 import lxml.etree → DLL load failed，docx 导出 500）
        (r"E:/ANACONDA/Library/bin/libxml2.dll", "."),
        (r"E:/ANACONDA/Library/bin/libxslt.dll", "."),
        (r"E:/ANACONDA/Library/bin/libexslt.dll", "."),
        (r"E:/ANACONDA/Library/bin/iconv.dll", "."),
        (r"E:/ANACONDA/Library/bin/zlib.dll", "."),
        (r"E:/ANACONDA/Library/bin/zlib-ng2.dll", "."),
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
