@echo off
REM 调试启动.bat —— 调试入口：带控制台窗口、保留 RDKit 解析日志（App 内默认静默 rdApp.*，见 gradio_app.py）
REM 正常使用请双击 启动COF推荐.vbs（单实例+重启语义）；本脚本不管理 PID 文件
cd /d "C:\Users\ckx\Desktop\全新机器学习实验"
set COF_RDKIT_DEBUG=1
start "COF App" "E:\ANACONDA\python.exe" app\gradio_app.py
