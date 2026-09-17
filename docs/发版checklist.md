# 发版 Checklist（本批次 7 项修复 + UI 批次）

> 使用者：本机发版操作者（agent）。发版前必须经用户确认，**版本号由用户指定**。
> 分支：`fix/v1.9.3-diagnosis-batch`（12+ 提交）；回滚点 tag：`pre-v1.9.3-backup`。

## 0. 前置确认（缺一不可）

- [ ] 用户已明确给出**版本号**与**发版确认**（这是本项目的硬性纪律）。
- [ ] `git status` 干净（仅允许 `ppt/` 等既有未跟踪项）；全部测试通过。
- [ ] 无遗留进程占用产物：`Get-Process cof-backend` 必须为空（否则 PyInstaller
      清目录时会 `[WinError 5] 拒绝访问`，本轮已踩过）。

## 1. 版本号三处同步（改完再构建）

| 位置 | 说明 |
|------|------|
| `README.md` 第 5 行 | `- **最新版本**：**vX.Y.Z** → ...` |
| `api/__init__.py` | `__version__ = "X.Y.Z"`（发版纪律第 7 条，须与前端同步） |
| `webapp/package.json` | `"version": "X.Y.Z"`（electron-builder 用它命名安装包） |

## 2. 前端构建

```powershell
cd webapp; npm run build      # tsc -b && vite build，必须 exit 0
```

## 3. 后端打包（**关键：必须清 PyInstaller 缓存**）

```powershell
cd <项目根>
Remove-Item build\cof-backend -Recurse -Force      # ← 不清会复用陈旧 TOC
Remove-Item dist-backend\cof-backend -Recurse -Force
E:\ANACONDA\python.exe -m PyInstaller scripts/cof-backend.spec --distpath dist-backend --noconfirm
```

**为什么必须清缓存**：前端 `dist` 产物名带 hash。若复用旧 TOC，冻结产物里的
`_internal/webapp/dist` 仍是上一版资源（本轮实测出现过「只带旧 index.html + 1 个 CSS
→ 打包后白屏」）。清缓存后必须核对：

```powershell
# 文件清单一致 + index.html 哈希一致（两者都要过）
Get-ChildItem dist-backend\cof-backend\_internal\webapp\dist -Recurse -File
(Get-FileHash webapp\dist\index.html -Algorithm MD5).Hash -eq `
(Get-FileHash dist-backend\cof-backend\_internal\webapp\dist\index.html -Algorithm MD5).Hash
```

> 观测记录：PyInstaller 6.21 构建成功时退出码为 **0**；若同一命令行里 `Remove-Item`
> 因文件被占用而失败（见 §0 的进程检查），整条命令会返回非 0。**判定以产物 + 自证为准**。

## 4. 冻结版自证（必须全绿）

```powershell
# 脚本自带：隔离 COF_DATA_DIR、起 exe --port 8901、跑完自动 taskkill
$env:SELFCERT_DATA="E:\cof-build\selfcert193"
E:\ANACONDA\python.exe .tmp_xtb\frozen_selfcert_v193.py     # 期望 9/9 通过

# v1.9.6 起：再跑一遍「本版新功能专项自证」（同一个 exe，另起端口）
$env:SELFCERT_DATA="E:\cof-build\selfcert196_extra"
E:\ANACONDA\python.exe .tmp_xtb\frozen_selfcert_v195_extra.py   # 期望 12/12 通过
```

覆盖：health / 文献录入+编号+文献节点入图 / `literature.attachments`（主文+SI）/
`annotate_entries` 计数 / 条目入库入图 / `PATCH /papers` 只补空 /
`records.dates` experiment_date / `assistant.research` 新 attachments 参数无 ImportError /
侧车图含文献节点+组节点+`reaction_cited_in` 边。

**专项自证为什么必要**：路由内**惰性 import** 的新模块（`literature.pdf_figures`、
`literature.vision` 等）PyInstaller 静态分析抓不到，spec 里靠 `hiddenimports` 兜。
只跑标准自证会漏掉「用户侧静默 ImportError」。专项自证在真实 exe 上端到端验证：
文献图抽取（内嵌图+图注配对+类型）、暂存图预览、候选图入图谱、视觉读图未启用时
400 并指向设置页、`llm-settings` 暴露 `vision_status`、前端资源含本版新样式/开关。
**发新版时把新功能加进这个脚本**（历史版本号留在脚本名里不影响复用）。

## 5. Electron 安装包

```powershell
cd webapp
# v1.9.6 实测：本机 TLS 被中间证书拦截，electron-builder 下载 Electron 发行包会报
# 「unable to verify the first certificate」；缓存里也没有 electron 目录。
# 用本地已有发行版可完全免下载：
npx electron-builder --win nsis --config.directories.output=release `
    --config.electronDist=node_modules/electron/dist
```

> 旧写法 `-c.directories.output=C:/cof-build/release` 会被当成**配置文件路径**
> （ENOENT），必须用长参数 `--config.directories.output=`。

## 5b. 发布 zip（**必须校验内容**）

```powershell
cd webapp\release
# ① 先把 exe 拷到暂存目录（避免与构建/杀软扫描抢文件）
$stage = Join-Path $env:TEMP "zipstage"; Remove-Item $stage -Recurse -Force -EA SilentlyContinue
New-Item -ItemType Directory $stage | Out-Null
Copy-Item cof-film-recommend-setup-X.Y.Z.exe $stage -Force
# ② 压缩暂存副本，再移回 release
Compress-Archive -Path (Join-Path $stage "cof-film-recommend-setup-X.Y.Z.exe") `
                 -DestinationPath (Join-Path $stage "out.zip") -CompressionLevel Optimal
Move-Item (Join-Path $stage "out.zip") COF-Assistant-Setup-X.Y.Z-win-x64.zip -Force
# ③ 解压校验：必须与源 exe MD5 一致，且体积 ≈ 安装包
Expand-Archive COF-Assistant-Setup-X.Y.Z-win-x64.zip -DestinationPath $env:TEMP\zipverify -Force
(Get-FileHash $env:TEMP\zipverify\cof-film-recommend-setup-X.Y.Z.exe -Algorithm MD5).Hash
```

> **v1.9.6 实测踩坑**：构建后立刻 `Compress-Archive -Path <exe>` 会读到**残缺内容 →
> 只生成 6.9MB 的坏 zip**（源文件仍被占用/扫描）；`ZipFile::CreateFromDirectory`
> 更糟——会把整个 `release/`（含 win-unpacked）当输入并因自占用失败，留下垃圾包。
> 因此**必须**「先拷副本 → 压缩副本 → 解压对比 MD5」，不可省。


产物：`cof-film-recommend-setup-X.Y.Z.exe`（约 456MB）；随后压缩 `dist-backend/cof-backend`
为 zip（压缩前等杀软释放句柄，失败就重试）。

## 6. 打 tag + 建 Release + 上传（顺序固定）

```powershell
git add -A; git commit -m "release: vX.Y.Z"; git push
git tag vX.Y.Z; git push origin vX.Y.Z
# 建 Release（脚本读 token：环境变量 GITHUB_TOKEN 或 .tmp_xtb/github_token.txt）
E:\ANACONDA\python.exe build\create_release_vXXX.py
# 上传顺序：exe → zip → **latest.yml 最后**（避免自动更新抢先指向未上传完的包）
E:\ANACONDA\python.exe .tmp_xtb\upload_asset.py
```
上传后**必须复核资产清单**（3 个：exe / zip / latest.yml 都在，且 latest.yml 最后写入）。

## 7. 本机安装与验收

```powershell
Start-Process webapp\release\cof-film-recommend-setup-X.Y.Z.exe -ArgumentList "/S" -Verb RunAs -Wait
```
（UAC 必须由**用户**批准。）安装后验收：

- `E:\COFapp\cof-film-recommend\COF科研助手.exe` FileVersion = X.Y.Z；注册表 DisplayVersion 同步；
- 启动后 `/api/health` 返回 `version=X.Y.Z`；文献/记录/助手等关键端点可用；
- 抽查本轮修复：文献附件（主文+SI）、补解析、实验时间显示、助手拖拽调宽、引用链接。

## 8. 凭据与安全（本轮新增）

- GitHub PAT **不再明文写入脚本**：脚本统一调用 `_load_github_token()`，优先读
  环境变量 `GITHUB_TOKEN`，其次读 `.tmp_xtb/github_token.txt`（该目录已 gitignore）。
- 已核实：公开仓库（`kimi-tianxuan-seek-APP` 为 **public**）的已跟踪文件中**没有**
  完整 token（历史日报里只有 4 字符前缀 `github_pat_11CB...`，不可用）。
- 新增回归防线 `tests/test_no_secrets.py`：扫描 git 跟踪文件，命中完整密钥形态即失败。
- **建议用户轮换该 PAT**（本地脚本曾有多份明文副本；虽然未入库，轮换是零成本保险）。

## 9. 回滚

- 代码：`git revert` 或在 tag `pre-v1.9.3-backup` 上重建；
- 应用：重装上一版 `cof-film-recommend-setup-<上一版>.exe`（Release 资产仍在）；
- 用户数据：本轮除新增 `data/literature/pdfs/`（附件）外未改动既有数据格式；
  `date` 语义保持不变（实验时间仅派生展示），回滚安全。
