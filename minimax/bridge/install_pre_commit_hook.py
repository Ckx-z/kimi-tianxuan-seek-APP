"""安装 pre-commit hook 到 .git/hooks/"""
import subprocess
import os

PROJ = r'C:\Users\ckx\Desktop\minimax'
hook_dir = os.path.join(PROJ, '.git', 'hooks')

# Windows: 写 pre-commit.bat
bat_path = os.path.join(hook_dir, 'pre-commit.bat')
bat_content = r'''@echo off
python C:\Users\ckx\Desktop\minimax\bridge\pre_commit_check.py
exit /b %ERRORLEVEL%
'''
with open(bat_path, 'w', encoding='utf-8') as f:
    f.write(bat_content)
print(f'Created: {bat_path}')

# Git Bash (sh) 版本
sh_path = os.path.join(hook_dir, 'pre-commit')
sh_content = r'''#!/bin/sh
python /c/Users/ckx/Desktop/minimax/bridge/pre_commit_check.py
'''
with open(sh_path, 'w', encoding='utf-8') as f:
    f.write(sh_content)
print(f'Created: {sh_path}')

# 清理 staged 测试文件 (如果有)
subprocess.run(['git', '-C', PROJ, 'reset', 'HEAD', '_tmp_test_secret.py'], capture_output=True, text=True)
test_file = os.path.join(PROJ, '_tmp_test_secret.py')
if os.path.exists(test_file):
    os.remove(test_file)

print('Hooks 已安装 + 测试文件清理')