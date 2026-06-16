#!/usr/bin/env bash
# ============================================================
# Claude Code 智能启动器
# 用法：cd ~/virtual-hospital && ./claude-code.sh
#
# 运行纪律：
#   GPU 空闲 → 自动使用本地模型（零云端消耗）
#   GPU 被占 → 自动回退到云端 API（不争显存）
#   生产环境（评估管道）永远是 GPU 的优先使用方
#   Claude Code 是谦让方，绝不与生产环境争抢显存
# ============================================================
set -uo pipefail

# ---- 配置 ----
LOCAL_MODEL="${CLAUDE_CODE_MODEL:-gemma4:31b}"  # 默认本地模型，可通过环境变量覆盖
GPU_THRESHOLD_MB=5000                           # 显存占用超过此值（MB）视为"GPU 被占"
                                                # keep_alive=0 下空闲时约 0-500MB
                                                # 评估运行时 meditron 占 ~38000MB

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; NC='\033[0m'

# ---- 检查 nvidia-smi 可用 ----
if ! command -v nvidia-smi &>/dev/null; then
    echo -e "${YELLOW}!${NC} nvidia-smi 不可用，无法检测 GPU 状态"
    echo -e "${YELLOW}!${NC} 回退到云端 API"
    exec claude
fi

# ---- 检测 GPU 显存占用 ----
gpu_used_mb=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null \
    | awk '{sum+=$1} END{print int(sum)}')
gpu_total_mb=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null \
    | awk '{sum+=$1} END{print int(sum)}')

if [ -z "$gpu_used_mb" ] || [ -z "$gpu_total_mb" ]; then
    echo -e "${YELLOW}!${NC} 无法读取 GPU 显存信息"
    echo -e "${YELLOW}!${NC} 回退到云端 API"
    exec claude
fi

gpu_pct=$((gpu_used_mb * 100 / gpu_total_mb))

# ---- 决策 ----
echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║      Claude Code 智能启动器                  ║"
echo "╚══════════════════════════════════════════════╝"
echo ""
echo "  GPU 显存：${gpu_used_mb}MB / ${gpu_total_mb}MB（${gpu_pct}%）"
echo "  阈值：${GPU_THRESHOLD_MB}MB"
echo ""

if [ "$gpu_used_mb" -gt "$GPU_THRESHOLD_MB" ]; then
    echo -e "  ${YELLOW}▶ GPU 被占用（可能有模型在运行）${NC}"
    echo -e "  ${YELLOW}▶ 自动使用云端 API（不争显存）${NC}"
    echo ""
    echo "  提示：如需使用本地模型，请先确保评估管道空闲："
    echo "    nvidia-smi  # 查看谁在占用"
    echo ""
    exec claude
else
    echo -e "  ${GREEN}▶ GPU 空闲${NC}"
    echo -e "  ${GREEN}▶ 使用本地模型：${LOCAL_MODEL}${NC}"
    echo ""
    echo "  注意：评估管道运行期间请勿同时使用 Claude Code"
    echo "  如需切换到云端：直接运行 claude（不带本脚本）"
    echo ""

    # 检查宿主机 ollama 是否在运行
    if ! pgrep -x ollama &>/dev/null; then
        echo -e "  ${YELLOW}!${NC} 宿主机 ollama 未运行，正在启动..."
        ollama serve &>/dev/null &
        sleep 3
    fi

    exec ollama launch claude --model "$LOCAL_MODEL"
fi
