#!/usr/bin/env bash
# ════════════════════════════════════════════════════════
# 虚拟医院全量备份 — 离线硬盘目标
#
# 设计：面向手动插拔的离线硬盘。流程为
#   检测挂载 → 转储三类资产 → restic 加密快照 → 校验 → 提示卸载
# 离线硬盘未挂载时拒绝运行，不静默失败。
#
# 用法：
#   sudo ./backup.sh /mnt/backup-disk
# 其中 /mnt/backup-disk 为离线硬盘的挂载点。
# ════════════════════════════════════════════════════════
set -euo pipefail

# ── 参数与配置 ──
MOUNT_POINT="${1:-}"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAGING="${PROJECT_DIR}/backup/.staging"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"

# restic 仓库密码从环境变量或密码文件读取，绝不硬编码
RESTIC_PASSWORD_FILE="${RESTIC_PASSWORD_FILE:-${PROJECT_DIR}/backup/.restic-password}"

# 容器名（与 docker-compose 一致）
PG_CONTAINER="vh-postgres"
MINIO_CONTAINER="vh-minio"
OLLAMA_CONTAINER="vh-ollama"

# 从 .env 读取数据库凭据
if [[ -f "${PROJECT_DIR}/.env" ]]; then
    set -a; source "${PROJECT_DIR}/.env"; set +a
fi
PG_USER="${PG_USER:-yangrenming}"

# ── 前置校验 ──
err() { echo "[错误] $*" >&2; exit 1; }
info() { echo "[$(date +%H:%M:%S)] $*"; }

[[ -n "$MOUNT_POINT" ]] || err "用法：$0 <离线硬盘挂载点>，如 $0 /mnt/backup-disk"
[[ -d "$MOUNT_POINT" ]] || err "挂载点 $MOUNT_POINT 不存在"

# 关键：确认离线硬盘确实已挂载（而非写入到挂载点下的本地空目录）
if ! mountpoint -q "$MOUNT_POINT"; then
    err "$MOUNT_POINT 未挂载任何设备。请先插入并挂载离线硬盘。"
fi

# 确认 restic 已安装
command -v restic >/dev/null || err "未找到 restic，请先安装：apt install restic"

# 确认密码文件存在
[[ -f "$RESTIC_PASSWORD_FILE" ]] || err "restic 密码文件不存在：$RESTIC_PASSWORD_FILE（见 README 初始化步骤）"

export RESTIC_REPOSITORY="${MOUNT_POINT}/virtual-hospital-restic"
export RESTIC_PASSWORD_FILE

# ── 初始化 staging 区 ──
rm -rf "$STAGING"
mkdir -p "$STAGING/db" "$STAGING/minio" "$STAGING/models"

cleanup() { rm -rf "$STAGING"; }
trap cleanup EXIT

# ── 1. PostgreSQL 全量转储（含所有 schema：档案/知识库/规则/审计 + Authentik 库）──
info "转储 PostgreSQL（主库 + Authentik 库）…"
docker exec "$PG_CONTAINER" pg_dumpall -U "$PG_USER" \
    | gzip > "$STAGING/db/pg_dumpall_${TIMESTAMP}.sql.gz"
info "  数据库转储完成：$(du -h "$STAGING/db/"*.sql.gz | cut -f1)"

# ── 2. MinIO 对象数据（原始 PDF / 影像 / 检验报告）──
info "复制 MinIO 数据卷…"
docker run --rm \
    --volumes-from "$MINIO_CONTAINER" \
    -v "$STAGING/minio:/backup" \
    alpine sh -c "cd /data && tar czf /backup/minio_${TIMESTAMP}.tar.gz ."
info "  MinIO 数据完成：$(du -h "$STAGING/minio/"*.tar.gz | cut -f1)"

# ── 3. Ollama 自定义模型（体积大，用户选择完整备份）──
info "复制 Ollama 模型（体积较大，请耐心等待）…"
docker run --rm \
    --volumes-from "$OLLAMA_CONTAINER" \
    -v "$STAGING/models:/backup" \
    alpine sh -c "cd /root/.ollama && tar czf /backup/ollama_${TIMESTAMP}.tar.gz ."
info "  模型完成：$(du -h "$STAGING/models/"*.tar.gz | cut -f1)"

# ── 4. restic 加密快照 ──
# 仓库不存在则初始化
if ! restic snapshots >/dev/null 2>&1; then
    info "首次使用，初始化 restic 加密仓库…"
    restic init
fi

info "创建 restic 加密快照…"
restic backup "$STAGING" \
    --tag "full-${TIMESTAMP}" \
    --host virtual-hospital

# ── 5. 完整性校验 ──
info "校验快照完整性…"
restic check --read-data-subset=10%

# ── 6. 保留策略：保留最近 7 个全量 + 4 周 + 6 月 ──
info "应用保留策略并清理旧快照…"
restic forget --keep-last 7 --keep-weekly 4 --keep-monthly 6 --prune

info "快照列表："
restic snapshots --compact

# ── 完成 ──
sync
echo ""
echo "════════════════════════════════════════════"
echo " 备份完成。请安全卸载离线硬盘后断电拔出："
echo "   sudo umount $MOUNT_POINT"
echo "════════════════════════════════════════════"
