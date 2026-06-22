#!/usr/bin/env bash
# ════════════════════════════════════════════════════════
# 虚拟医院恢复 — 从离线硬盘还原
#
# 未经恢复验证的备份等于没有备份。建议每季度演练一次。
#
# 用法：
#   列出可用快照：  sudo ./restore.sh /mnt/backup-disk list
#   恢复指定快照：  sudo ./restore.sh /mnt/backup-disk restore <snapshot-id>
# ════════════════════════════════════════════════════════
set -euo pipefail

MOUNT_POINT="${1:-}"
ACTION="${2:-list}"
SNAPSHOT="${3:-latest}"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RESTORE_DIR="${PROJECT_DIR}/backup/.restore"
RESTIC_PASSWORD_FILE="${RESTIC_PASSWORD_FILE:-${PROJECT_DIR}/backup/.restic-password}"

PG_CONTAINER="vh-postgres"
MINIO_CONTAINER="vh-minio"
OLLAMA_CONTAINER="vh-ollama"

if [[ -f "${PROJECT_DIR}/.env" ]]; then set -a; source "${PROJECT_DIR}/.env"; set +a; fi
PG_USER="${PG_USER:-yangrenming}"

err() { echo "[错误] $*" >&2; exit 1; }
info() { echo "[$(date +%H:%M:%S)] $*"; }

[[ -n "$MOUNT_POINT" ]] || err "用法：$0 <挂载点> [list|restore] [snapshot-id]"
mountpoint -q "$MOUNT_POINT" || err "$MOUNT_POINT 未挂载，请先插入离线硬盘"
command -v restic >/dev/null || err "未找到 restic"
[[ -f "$RESTIC_PASSWORD_FILE" ]] || err "restic 密码文件不存在"

export RESTIC_REPOSITORY="${MOUNT_POINT}/virtual-hospital-restic"
export RESTIC_PASSWORD_FILE

# ── 列出快照 ──
if [[ "$ACTION" == "list" ]]; then
    restic snapshots
    echo ""
    echo "恢复命令：sudo $0 $MOUNT_POINT restore <snapshot-id>"
    exit 0
fi

[[ "$ACTION" == "restore" ]] || err "未知操作：$ACTION（应为 list 或 restore）"

# ── 恢复确认 ──
echo "════════════════════════════════════════════"
echo " 警告：恢复将覆盖当前数据库、文件与模型。"
echo " 快照：$SNAPSHOT"
echo "════════════════════════════════════════════"
read -rp "确认恢复？输入 yes 继续：" confirm
[[ "$confirm" == "yes" ]] || err "已取消"

# ── 从 restic 提取到临时目录 ──
rm -rf "$RESTORE_DIR"; mkdir -p "$RESTORE_DIR"
trap 'rm -rf "$RESTORE_DIR"' EXIT

info "从 restic 提取快照 $SNAPSHOT…"
restic restore "$SNAPSHOT" --target "$RESTORE_DIR"
STAGE="$(find "$RESTORE_DIR" -type d -name .staging | head -1)"
[[ -n "$STAGE" ]] || err "快照中未找到 staging 数据"

# ── 1. 恢复 PostgreSQL ──
info "恢复 PostgreSQL…"
DB_DUMP="$(find "$STAGE/db" -name '*.sql.gz' | head -1)"
gunzip -c "$DB_DUMP" | docker exec -i "$PG_CONTAINER" psql -U "$PG_USER" -d postgres
info "  数据库恢复完成"

# ── 2. 恢复 MinIO ──
info "恢复 MinIO 对象数据…"
MINIO_TAR="$(find "$STAGE/minio" -name '*.tar.gz' | head -1)"
docker run --rm --volumes-from "$MINIO_CONTAINER" \
    -v "$STAGE/minio:/backup" \
    alpine sh -c "cd /data && tar xzf /backup/$(basename "$MINIO_TAR")"
info "  MinIO 恢复完成"

# ── 3. 恢复 Ollama 模型 ──
info "恢复 Ollama 模型…"
OLLAMA_TAR="$(find "$STAGE/models" -name '*.tar.gz' | head -1)"
docker run --rm --volumes-from "$OLLAMA_CONTAINER" \
    -v "$STAGE/models:/backup" \
    alpine sh -c "cd /root/.ollama && tar xzf /backup/$(basename "$OLLAMA_TAR")"
info "  模型恢复完成"

echo ""
echo "════════════════════════════════════════════"
echo " 恢复完成。建议重启服务栈：docker compose restart"
echo "════════════════════════════════════════════"
