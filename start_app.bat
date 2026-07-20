@echo off
cd /d "C:\Users\ckx\Desktop\全新机器学习实验"
REM 调试入口：保留 RDKit 解析日志（App 内默认静默 rdApp.*，见 gradio_app.py）
set COF_RDKIT_DEBUG=1
start "COF App" python app/gradio_app.py
