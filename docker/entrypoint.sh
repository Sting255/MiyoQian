#!/bin/sh
set -e

CONFIG_PATH="${MIYOUQIAN_CONFIG_PATH:-/app/state/config.yaml}"
CONFIG_DIR="$(dirname "$CONFIG_PATH")"

umask 077

# 创建必要的目录
mkdir -p "$CONFIG_DIR" "$CONFIG_DIR/data" "$CONFIG_DIR/logs"

# 目录不可写时给一句能看懂的提示，而不是让后面一堆 Permission denied 刷屏
# （常见于把宿主机 root 拥有的目录 bind mount 进 /app/state）
if ! touch "$CONFIG_DIR/.write-test" 2>/dev/null; then
  echo "[entrypoint] 状态目录不可写: $CONFIG_DIR"
  echo "[entrypoint] 如果是 bind mount，请确保它属于容器内的 $(id -u):$(id -g)，或改用命名卷"
  exit 1
fi
rm -f "$CONFIG_DIR/.write-test"

# 如果 config.yaml 不存在，从示例配置复制
if [ ! -f "$CONFIG_PATH" ]; then
  echo "[entrypoint] config.yaml not found, copying from config.example.yaml"
  cp /app/config.example.yaml "$CONFIG_PATH"
fi

chmod 700 "$CONFIG_DIR" "$CONFIG_DIR/data" "$CONFIG_DIR/logs"
chmod 600 "$CONFIG_PATH"

exec "$@"
