-- Authentik 需要独立数据库（与虚拟医院主库隔离）
-- 此脚本以 00 前缀确保最先执行
CREATE DATABASE authentik;
