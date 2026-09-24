#!/usr/bin/env bash
# 一键启动脚本（Linux / macOS / WSL）
set -e

# 切到项目根目录：本脚本在 scripts/ 下，config.yaml 和 main.py 都在上一层
cd "$(dirname "$0")/.."

echo "===== 米游签一键启动 ====="

# 检查 Python
if ! command -v python3 >/dev/null 2>&1; then
    echo "[错误] 没找到 python3，请先安装 Python 3.11+"
    exit 1
fi

# 检查 uv
if ! command -v uv >/dev/null 2>&1; then
    echo "[提示] 正在安装 uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # 现在的安装器装到 ~/.local/bin（~/.cargo/bin 是 uv 0.4 时代的旧位置），
    # 两个都加上，保证下面的 uv 命令能直接找到。
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi

if ! command -v uv >/dev/null 2>&1; then
    echo "[错误] uv 安装后仍找不到，请重新打开终端再试（或手动把 ~/.local/bin 加进 PATH）"
    exit 1
fi

# 复制配置（如未存在）
if [ ! -f config.yaml ]; then
    echo "[提示] 复制 config.example.yaml 到 config.yaml"
    cp config.example.yaml config.yaml
fi

# 创建虚拟环境并安装依赖
if [ ! -d .venv ]; then
    echo "[提示] 创建虚拟环境..."
    uv venv --python 3.11
fi

echo "[提示] 安装/更新依赖..."
uv sync

# 启动 Web 控制台
echo ""
echo "===== 启动 Web 控制台 ====="
echo "浏览器打开: http://127.0.0.1:5890"
echo ""
uv run python main.py