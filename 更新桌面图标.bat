@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
REM ============================================================
REM  更新桌面图标.bat
REM  功能：把桌面旧快捷方式「COF成膜推荐.lnk」（原指向老 Gradio
REM  启动器 启动COF推荐.vbs）重定向到已安装的 COF成膜推荐系统.exe。
REM  找不到安装时提示先运行安装包。不改动 启动COF推荐.vbs 本身。
REM ============================================================

set "EXE_NAME=COF成膜推荐系统.exe"
set "FOUND_EXE="

REM ---- 1) 探测常见安装目录 ----
for %%D in (
  "%LOCALAPPDATA%\Programs\COF成膜推荐系统"
  "%LOCALAPPDATA%\Programs\cof-film-recommend"
  "%ProgramFiles%\COF成膜推荐系统"
  "%ProgramFiles(x86)%\COF成膜推荐系统"
) do (
  if not defined FOUND_EXE if exist "%%~D\%EXE_NAME%" set "FOUND_EXE=%%~D\%EXE_NAME%"
)

REM ---- 2) 探测开始菜单快捷方式（用户级 / 公共） ----
if not defined FOUND_EXE (
  for %%S in (
    "%APPDATA%\Microsoft\Windows\Start Menu\Programs\COF成膜推荐系统"
    "%ProgramData%\Microsoft\Windows\Start Menu\Programs\COF成膜推荐系统"
  ) do (
    if not defined FOUND_EXE if exist "%%~S" (
      for %%L in ("%%~S\*.lnk") do (
        if not defined FOUND_EXE (
          for /f "usebackq delims=" %%T in (`powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%%~fL'); if ($s.TargetPath -like '*%EXE_NAME%') { $s.TargetPath }"`) do (
            if not defined FOUND_EXE set "FOUND_EXE=%%T"
          )
        )
      )
    )
  )
)

if not defined FOUND_EXE (
  echo.
  echo [未找到安装] 未检测到已安装的 %EXE_NAME%。
  echo 请先运行安装包 COF成膜推荐系统-Setup-x.x.x.exe 完成安装，再运行本脚本。
  echo.
  pause
  exit /b 1
)

echo 已找到安装程序: !FOUND_EXE!

REM ---- 3) 重写桌面快捷方式 ----
set "LNK=%USERPROFILE%\Desktop\COF成膜推荐.lnk"
if not exist "%LNK%" (
  echo [提示] 桌面没有找到 COF成膜推荐.lnk，将直接新建一个同名快捷方式。
)

for %%I in ("!FOUND_EXE!") do set "EXE_DIR=%%~dpI"

powershell -NoProfile -Command ^
  "$ws = New-Object -ComObject WScript.Shell;" ^
  "$sc = $ws.CreateShortcut('%LNK%');" ^
  "$sc.TargetPath = '!FOUND_EXE!';" ^
  "$sc.WorkingDirectory = '!EXE_DIR!';" ^
  "$sc.Arguments = '';" ^
  "$sc.IconLocation = '!FOUND_EXE!,0';" ^
  "$sc.Description = 'COF 成膜推荐系统';" ^
  "$sc.Save()"

if errorlevel 1 (
  echo [失败] 重写快捷方式时出错，请右键“以管理员身份运行”本脚本后重试。
  pause
  exit /b 1
)

echo.
echo [完成] 桌面快捷方式「COF成膜推荐.lnk」已指向:
echo   !FOUND_EXE!
echo 旧的 启动COF推荐.vbs 未被改动，可手动删除。
echo.
pause
exit /b 0
