# UI 一键回退脚本（v1.9.4）
#
# 背景：本机安装版（E:\COFapp）的界面是「冻结后端内置的静态资源」
#       （resources\backend\_internal\webapp\dist）。UI 迭代只改这一份静态资源，
#       因此回退 = 把某个快照的 dist 覆盖回去 + 重启应用，**不碰用户数据**。
#
# 用法（在项目根或任意位置）：
#   pwsh scripts\rollback_ui.ps1 -List                 # 列出所有 UI 快照
#   pwsh scripts\rollback_ui.ps1                       # 回退到最近一个快照
#   pwsh scripts\rollback_ui.ps1 -Snapshot baseline-20260912-195107   # 指定快照
#   pwsh scripts\rollback_ui.ps1 -KeepRunning          # 回退但不自动重启应用
#
# 整包回退（含后端与依赖）请直接重装安装包：
#   C:\Users\ckx\Desktop\全新机器学习实验\webapp\release\cof-film-recommend-setup-1.9.3.exe
#   （或从 GitHub Release 下载任意历史版本）

param(
  [string]$Snapshot = "",
  [string]$AppRoot = "E:\COFapp\cof-film-recommend",
  [string]$SnapshotRoot = "E:\cof-build\ui-snapshots",
  [switch]$List,
  [switch]$KeepRunning
)

$ErrorActionPreference = "Stop"
$dist = Join-Path $AppRoot "resources\backend\_internal\webapp\dist"
$exe = Join-Path $AppRoot "COF科研助手.exe"

if (-not (Test-Path $SnapshotRoot)) { throw "快照目录不存在：$SnapshotRoot" }
$snaps = Get-ChildItem $SnapshotRoot -Directory | Sort-Object Name -Descending

if ($List -or $snaps.Count -eq 0) {
  Write-Host "可用 UI 快照（新→旧）："
  foreach ($s in $snaps) {
    $n = (Get-ChildItem (Join-Path $s.FullName "dist") -Recurse -File -ErrorAction SilentlyContinue |
          Measure-Object).Count
    Write-Host ("  {0}   ({1} 个文件)   {2}" -f $s.Name, $n, $s.LastWriteTime)
  }
  if ($snaps.Count -eq 0) { Write-Host "  （无快照：尚未做过 UI 快照）" }
  return
}

$target = if ($Snapshot) {
  $snaps | Where-Object { $_.Name -eq $Snapshot } | Select-Object -First 1
} else { $snaps | Select-Object -First 1 }

if (-not $target) { throw "未找到快照：$Snapshot" }
$src = Join-Path $target.FullName "dist"
if (-not (Test-Path $src)) { throw "快照缺少 dist 目录：$src" }

# 回退前先备份「当前」资源，便于再次切回
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$pre = Join-Path $SnapshotRoot "pre-rollback-$stamp"
New-Item -ItemType Directory -Path $pre -Force | Out-Null
if (Test-Path $dist) { Copy-Item $dist (Join-Path $pre "dist") -Recurse -Force }

Write-Host "关闭应用…"
Get-Process | Where-Object { $_.ProcessName -match "COF|cof-backend" } |
  Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 3

Write-Host "恢复 UI 资源：$($target.Name)"
if (Test-Path $dist) { Remove-Item $dist -Recurse -Force -ErrorAction SilentlyContinue }
Copy-Item $src $dist -Recurse -Force

$a = (Get-FileHash (Join-Path $src "index.html") -Algorithm MD5).Hash
$b = (Get-FileHash (Join-Path $dist "index.html") -Algorithm MD5).Hash
if ($a -ne $b) { throw "回退校验失败：index.html 哈希不一致" }
Write-Host "回退完成（已备份回退前资源到 $pre）"

if (-not $KeepRunning) {
  Write-Host "重新启动应用…"
  Start-Process $exe
}
