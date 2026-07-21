# 调试启动.ps1 —— 调试入口：带控制台输出地启动 COF 成膜实验指导 App
# 正常使用请双击 启动COF推荐.vbs（单实例+重启语义）；本脚本不管理 PID 文件

Set-Location "C:\Users\ckx\Desktop\全新机器学习实验"
& "E:\ANACONDA\python.exe" app\gradio_app.py
