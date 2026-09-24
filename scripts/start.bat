@echo off
chcp 65001 >nul
REM 一键启动脚本（Windows）

REM 切到项目根目录：本脚本在 scripts\ 下，config.yaml 和 main.py 都在上一层
cd /d "%~dp0.."

echo ===== 米游签一键启动 =====

REM 检查 Python
where python >nul 2>&1
if errorlevel 1 (
    echo [错误] 没找到 python，请先安装 Python 3.11+ 并勾选 "Add to PATH"
    pause
    exit /b 1
)

REM 检查 uv
where uv >nul 2>&1
if errorlevel 1 (
    echo [提示] 正在安装 uv...
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    REM 安装器改的是用户级 PATH，当前 cmd 会话不会自动刷新，
    REM 所以这里手动把它加上（uv 现在装在 %USERPROFILE%\.local\bin）。
    set "PATH=%USERPROFILE%\.local\bin;%PATH%"
)

where uv >nul 2>&1
if errorlevel 1 (
    echo [错误] 装完 uv 还是找不到，请关掉这个窗口重新运行 scripts\start.bat
    pause
    exit /b 1
)

REM 复制配置
if not exist config.yaml (
    echo [提示] 复制 config.example.yaml 到 config.yaml
    copy /Y config.example.yaml config.yaml >nul
)

REM 创建虚拟环境
if not exist .venv (
    echo [提示] 创建虚拟环境...
    uv venv --python 3.11
)

echo [提示] 安装/更新依赖...
uv sync

echo.
echo ===== 启动 Web 控制台 =====
echo 浏览器打开: http://127.0.0.1:5890
echo.

uv run python main.py

pause