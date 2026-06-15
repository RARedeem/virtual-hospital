#!/usr/bin/env bash
# ============================================================
# 虚拟医院 一键启动脚本
# 用法：cd ~/virtual-hospital && ./start.sh
# 作用：按序拉起容器 → 等就绪 → 自检 GPU/模型 → 托管前端 → 汇总状态
# ============================================================
set -uo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

FRONTEND_PORT=5500
GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✓${NC} $*"; }
warn() { echo -e "${YELLOW}!${NC} $*"; }
err()  { echo -e "${RED}✗${NC} $*"; }
step() { echo -e "\n${YELLOW}==>${NC} $*"; }

# ---- 0. 前置检查 ----
step "前置检查"
command -v docker >/dev/null || { err "未找到 docker"; exit 1; }
docker compose version >/dev/null 2>&1 || { err "docker compose 不可用"; exit 1; }
[[ -f .env ]] || { err ".env 不存在，请先 cp .env.example .env 并填写密码"; exit 1; }
ok "docker / compose / .env 就绪"

# ---- 1. 拉起容器 ----
step "启动容器栈 (docker compose up -d)"
if docker compose up -d; then
    ok "compose up 完成"
else
    err "compose up 失败，查看： docker compose logs"
    exit 1
fi

# ---- 2. 等待关键服务健康 ----
step "等待服务就绪（最多 ~120 秒）"
wait_healthy() {
    local name="$1" tries=40
    for ((i=1;i<=tries;i++)); do
        local st
        st=$(docker inspect -f '{{.State.Health.Status}}' "$name" 2>/dev/null || echo "none")
        if [[ "$st" == "healthy" ]]; then ok "$name 健康"; return 0; fi
        if [[ "$st" == "none" ]]; then
            # 无健康检查的容器（如 orchestrator），看是否 running
            local run
            run=$(docker inspect -f '{{.State.Running}}' "$name" 2>/dev/null || echo "false")
            [[ "$run" == "true" ]] && { ok "$name 运行中（无健康检查）"; return 0; }
        fi
        sleep 3
    done
    warn "$name 未在预期时间内就绪（当前: ${st:-unknown}），继续但可能影响功能"
    return 1
}
wait_healthy vh-postgres
wait_healthy vh-ollama
wait_healthy vh-minio
wait_healthy vh-orchestrator

# ---- 3. GPU 自检（重启后最易出问题的一环）----
step "GPU 自检"
if docker exec vh-ollama nvidia-smi >/dev/null 2>&1; then
    gpu_count=$(docker exec vh-ollama nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | wc -l)
    ok "容器内可见 GPU：$gpu_count 张"
else
    err "容器内看不到 GPU！评估会因模型无法加载而失败（500）。"
    warn "排查：宿主机 nvidia-smi 是否正常 / NVIDIA Container Toolkit 是否就绪"
    warn "修复后重跑本脚本，或重建： docker compose up -d --force-recreate ollama"
fi

# ---- 4. 模型自检 ----
step "模型自检（应有 6 个）"
if model_list=$(docker exec vh-ollama ollama list 2>/dev/null); then
    cnt=$(echo "$model_list" | tail -n +2 | grep -c . || true)
    echo "$model_list" | tail -n +2 | awk '{print "    - "$1}'
    [[ "$cnt" -ge 6 ]] && ok "模型数量：$cnt" || warn "模型数量：$cnt（预期 6，缺失请跑 ./setup-models.sh）"
else
    warn "无法列出模型，ollama 可能还在加载"
fi

# ---- 5. 编排 API 自检 ----
step "编排 API 自检"
if curl -fs http://localhost:8000/health >/dev/null 2>&1; then
    ok "orchestrator /health 正常 (localhost:8000)"
else
    warn "orchestrator /health 暂无响应，可能还在启动，稍后重试"
fi

# ---- 6. 托管前端 ----
step "托管前端 (localhost:$FRONTEND_PORT)"
if lsof -i :"$FRONTEND_PORT" >/dev/null 2>&1; then
    ok "端口 $FRONTEND_PORT 已有服务在跑（可能是上次的前端），跳过"
else
    ( cd frontend && nohup python3 -m http.server "$FRONTEND_PORT" >/tmp/vh-frontend.log 2>&1 & )
    sleep 1
    if lsof -i :"$FRONTEND_PORT" >/dev/null 2>&1; then
        ok "前端已托管：http://localhost:$FRONTEND_PORT （日志 /tmp/vh-frontend.log）"
    else
        warn "前端托管未确认，手动跑： cd frontend && python3 -m http.server $FRONTEND_PORT"
    fi
fi

# ---- 7. 汇总 ----
step "启动完成 — 访问入口"
cat <<EOF
  前端（健康档案）  http://localhost:$FRONTEND_PORT   ← 用隐身窗口登录避免旧 token 缓存
  编排 API          http://localhost:8000
  Authentik 登录    http://localhost:9100
  MinIO 控制台      http://localhost:9001

  容器状态： docker compose ps
  实时日志： docker compose logs -f orchestrator
  停止全部： docker compose down   （数据保留在卷中，不丢）
EOF
echo ""
docker compose ps --format "table {{.Name}}\t{{.Status}}" 2>/dev/null || true
