@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

echo.
echo 正在寻找本机可用的 Python 3.10+ ...

set "PY_CMD="
for %%P in ("py -3" "python" "python3") do (
    if not defined PY_CMD (
        %%~P -c "import sys, sqlite3; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
        if !errorlevel! == 0 (
            set "PY_CMD=%%~P"
        )
    )
)

if not defined PY_CMD (
    echo [错误] 未检测到可用的 Python 3.10 及以上版本！
    echo 请先前往 https://www.python.org/downloads/ 安装 Python（安装时勾选 Add Python to PATH），再重新双击本脚本。
    echo.
    pause
    exit /b 1
)

%PY_CMD% "%~dp0install_helper.py" %*
set "RC=%errorlevel%"
echo.
if not "%RC%"=="0" (
    echo [安装失败] 请带上上方报错信息在 GitHub Issues 中反馈排查。
)
pause
exit /b %RC%
