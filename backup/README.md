# 备份目录

离线硬盘加密备份。覆盖三类资产：PostgreSQL（档案/知识库/规则/审计 + Authentik 库）、
MinIO 对象文件、Ollama 自定义模型。

## 初始化（首次）

```bash
# 1. 生成强随机 restic 仓库密码并妥善保管（丢失则备份永久无法解密）
openssl rand -base64 32 > backup/.restic-password
chmod 600 backup/.restic-password

# 重要：将此密码同时离线抄录保存一份。
# 它与备份硬盘分开存放——硬盘加密的意义在于硬盘丢失时数据不泄露，
# 若密码与硬盘存一起则失去意义。
```

## 备份流程（手动插拔离线硬盘）

```bash
# 1. 插入离线硬盘并挂载，例如：
sudo mount /dev/sdb1 /mnt/backup-disk

# 2. 执行备份
sudo ./backup/backup.sh /mnt/backup-disk

# 3. 按提示安全卸载后拔出
sudo umount /mnt/backup-disk
```

## 恢复流程

```bash
# 列出可用快照
sudo ./backup/restore.sh /mnt/backup-disk list

# 恢复指定快照（或 latest）
sudo ./backup/restore.sh /mnt/backup-disk restore <snapshot-id>
docker compose restart
```

## 安全设计要点

- restic 全程加密：硬盘即使丢失，无密码无法解密。
- 脚本强制校验离线硬盘已挂载（`mountpoint -q`），未挂载拒绝运行，
  避免误写到本地空目录造成"假备份"。
- 保留策略：最近 7 个全量 + 4 周 + 6 月，自动 prune 旧快照。
- 每次备份后 restic check 抽样校验 10% 数据完整性。

## 恢复演练

未经恢复验证的备份等于没有备份。建议每季度在一台测试机上执行一次
完整恢复演练，确认快照可用、数据完整。

## 关于自动化

离线硬盘平时不在线，无法用 cron 全自动。可选半自动方案：
配置 udev 规则，在识别到特定硬盘 UUID 插入时触发桌面通知，
提醒手动执行备份。该规则不在本骨架内，按需自行配置。
