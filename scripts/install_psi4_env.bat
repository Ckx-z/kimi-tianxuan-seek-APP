@echo off
rem =====================================================================
rem  Psi4 精度档（真 DFT）一键安装脚本
rem  供 COF 科研助手 App「DFT 计算 → 精度档」引导安装按钮调用，也可双击运行。
rem
rem  做的事：
rem    1. 定位本机 conda（参数 1 可显式传入 conda 安装根目录）
rem    2. conda create -n psi4-env -c conda-forge psi4 dftd3-python python=3.11 -y
rem       （dftd3-python 提供 D3BJ 色散校正的 Python 接口（psi4 必需）；
rem        约 300MB+ 下载，建议先开启网络加速器；过程可能 5-20 分钟）
rem    3. 安装后验证 import psi4 并打印版本
rem
rem  安装位置：<conda>\envs\psi4-env\python.exe
rem  App 自动探测该路径；如装在非常规位置，可在 config/runtime.local.json
rem  的 pythons.psi4 或环境变量 COF_PSI4_PYTHON 显式指定。
rem =====================================================================
setlocal EnableDelayedExpansion
chcp 65001 >nul

echo ============================================
echo   Psi4 精度档安装（真 DFT 计算引擎）
echo ============================================
echo.

rem ---------- 定位 conda ----------
set "CONDA_ROOT=%~1"
if not "%CONDA_ROOT%"=="" goto :found

for %%D in ("E:\ANACONDA" "%USERPROFILE%\anaconda3" "%USERPROFILE%\miniconda3" "C:\ProgramData\anaconda3" "C:\ProgramData\miniconda3" "D:\ANACONDA" "D:\anaconda3") do (
    if exist "%%~D\Scripts\conda.exe" (
        set "CONDA_ROOT=%%~D"
        goto :found
    )
)
where conda >nul 2>nul
if %errorlevel%==0 (
    for /f "delims=" %%i in ('where conda') do set "CONDA_EXE=%%i"& goto :found_which
)
echo [错误] 未找到 conda。请先安装 Anaconda/Miniconda，或把 conda 安装根目录作为参数传入：
echo        install_psi4_env.bat "C:\path\to\anaconda3"
pause
exit /b 1

:found
set "CONDA_EXE=%CONDA_ROOT%\Scripts\conda.exe"
:found_which
echo [信息] 使用 conda: %CONDA_EXE%
echo.

rem ---------- 幂等：已安装则直接验证 ----------
for %%E in ("E:\ANACONDA" "%USERPROFILE%\anaconda3" "%USERPROFILE%\miniconda3" "C:\ProgramData\anaconda3" "C:\ProgramData\miniconda3") do (
    if not "%CONDA_ROOT%"=="" goto :skip_env_scan
    if exist "%%~E\envs\psi4-env\python.exe" set "PSI4_PY=%%~E\envs\psi4-env\python.exe"& goto :verify
)
:skip_env_scan
if not "%CONDA_ROOT%"=="" if exist "%CONDA_ROOT%\envs\psi4-env\python.exe" (
    set "PSI4_PY=%CONDA_ROOT%\envs\psi4-env\python.exe"
    goto :verify
)

rem ---------- 创建环境 ----------
echo [步骤] 创建 psi4-env 环境（conda-forge，约 300MB+ 下载，请耐心等待）...
"%CONDA_EXE%" create -n psi4-env -c conda-forge psi4 dftd3-python python=3.11 -y
if %errorlevel% neq 0 (
    echo [错误] conda create 失败（网络问题居多，可开启加速器后重试本脚本）。
    pause
    exit /b 1
)

rem ---------- 定位新环境 python ----------
if not "%CONDA_ROOT%"=="" if exist "%CONDA_ROOT%\envs\psi4-env\python.exe" set "PSI4_PY=%CONDA_ROOT%\envs\psi4-env\python.exe"
if "%PSI4_PY%"=="" (
    for /f "delims=" %%i in ('"%CONDA_EXE%" info --base') do set "BASE=%%i"
    if exist "!BASE!\envs\psi4-env\python.exe" set "PSI4_PY=!BASE!\envs\psi4-env\python.exe"
)
if "%PSI4_PY%"=="" (
    echo [错误] 环境创建后未找到 psi4-env\python.exe，请手动检查 conda env list。
    pause
    exit /b 1
)

:verify
echo.
echo [步骤] 验证安装：%PSI4_PY%
"%PSI4_PY%" -c "import psi4, dftd3; print('Psi4 版本:', psi4.__version__, '| dftd3:', dftd3.__version__)"
if %errorlevel% neq 0 (
    echo [错误] import psi4 失败，安装不完整，请重试。
    pause
    exit /b 1
)

echo.
echo [完成] Psi4 精度档已就绪：%PSI4_PY%
echo        回到 App「DFT 计算」页选择「Psi4 精确」即可使用。
echo        若 App 未自动识别，请把上面的 python 路径填入
echo        config/runtime.local.json 的 pythons.psi4 后重启后端。
pause
exit /b 0
