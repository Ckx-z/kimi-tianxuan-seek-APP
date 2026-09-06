@echo off
REM Install local embedding environment for COF Research Assistant (v1.9.0)
REM 1) pip install sentence-transformers into dphuanjing (torch already present)
REM 2) download BAAI/bge-m3 from HuggingFace mirror to E:\cof_models\bge-m3
REM    (bge-m3 chosen over Qwen3-Embedding: dphuanjing is Python 3.8 and
REM     transformers>=4.51 required by Qwen3 no longer supports 3.8)
setlocal
set "PY=E:\ANACONDA\envs\dphuanjing\python.exe"
if not exist "%PY%" (
  echo [ERROR] dphuanjing interpreter not found: %PY%
  exit /b 1
)
echo === 1/2 install sentence-transformers ===
"%PY%" -m pip install -U sentence-transformers huggingface_hub || exit /b 1
echo === 2/2 download BAAI/bge-m3 (about 2.2GB, one-time) ===
"%PY%" -c "import os; os.environ['HF_ENDPOINT']='https://hf-mirror.com'; os.environ['HF_HUB_DISABLE_SYMLINKS']='1'; from huggingface_hub import snapshot_download; p = snapshot_download('BAAI/bge-m3', local_dir=r'E:\cof_models\bge-m3', ignore_patterns=['**/.DS_Store']); print('model downloaded:', p)"
if errorlevel 1 (
  echo [TIP] download failed. Retry or check network to hf-mirror.com.
)
echo === done: open Settings - Literature Parse LLM - embedding provider = local
echo     and set model path to E:\cof_models\bge-m3 ===
endlocal
