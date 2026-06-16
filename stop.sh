#!/usr/bin/env bash
# ============================================================
# 虚拟医院 停止脚本
# 用法：cd ~/virtual-hospital && ./stop.sh
# 作用：停掉前端托管进程 + 停掉所有容器（数据卷保留，不丢数据）
# ============================================================
set -uo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"
FRONTEND_PORT=5500

echo "==> 停止前端托管 (端口 $FRONTEND_PORT)"
if lsof -ti :"$FRONTEND_PORT" >/dev/null 2>&1; then
    lsof -ti :"$FRONTEND_PORT" | xargs -r kill && echo "  前端已停止"
else
    echo "  前端未在运行"
fi

echo "==> 停止容器栈 (docker compose down)"
docker compose down
echo ""
echo "已全部停止。数据保留在 docker 卷中（pg-data / minio-data / ollama-models），不会丢失。"
echo "下次启动： ./start.sh"
pkill -f "python3 -m http.server 5500" 2>/dev/null || true
