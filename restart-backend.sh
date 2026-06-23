#!/usr/bin/env bash
# 重启编排服务脚本

set -e

echo "正在杀死旧的后端进程..."
pkill -f "uvicorn app.main:app" || true
sleep 2

echo "启动新的后端进程..."
cd "$(dirname "$0")/orchestrator"

# 确保虚拟环境已激活（如果使用的话）
if [[ -d ../.venv ]]; then
    source ../.venv/bin/activate
fi

# 启动 uvicorn
/usr/local/bin/python3.12 -m uvicorn app.main:app --host 0.0.0.0 --port 8000 &

sleep 3
echo "✓ 后端已启动，监听 http://0.0.0.0:8000"
echo "✓ 现在上传文件应该能正常工作了"
