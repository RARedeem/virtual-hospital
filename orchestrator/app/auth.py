"""
认证授权模块。

认证：校验 Authentik 签发的 OIDC JWT（通过其 JWKS 公钥验签）。
授权：分级模型 —— admin 可访问全部成员档案，member 仅可访问自己。

授权逻辑落在本服务，而非 Authentik —— 因为"能看谁"依赖
本系统的 members 表关系，身份提供方无从知晓。
"""
import os
import time

import httpx
from fastapi import Depends, HTTPException, Request
from dataclasses import dataclass

# Authentik OIDC 配置
OIDC_ISSUER = os.environ.get("OIDC_ISSUER", "http://authentik:9000/application/o/virtual-hospital/")
OIDC_JWKS_URL = os.environ.get("OIDC_JWKS_URL", "http://authentik:9000/application/o/virtual-hospital/jwks/")
OIDC_AUDIENCE = os.environ.get("OIDC_AUDIENCE", "virtual-hospital")

# JWKS 公钥缓存（避免每次请求都拉取）
_jwks_cache: dict = {"keys": None, "fetched_at": 0}
_JWKS_TTL = 3600


@dataclass
class Principal:
    """已认证的登录主体。"""
    sub: str            # OIDC subject，对应 members.oidc_sub
    name: str
    member_id: str | None   # 关联的成员记录 id
    role: str               # admin / member


async def _get_jwks() -> dict:
    """获取并缓存 Authentik 的 JWKS 公钥集。"""
    now = time.time()
    if _jwks_cache["keys"] and now - _jwks_cache["fetched_at"] < _JWKS_TTL:
        return _jwks_cache["keys"]
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(OIDC_JWKS_URL)
        resp.raise_for_status()
        _jwks_cache["keys"] = resp.json()
        _jwks_cache["fetched_at"] = now
    return _jwks_cache["keys"]


async def verify_token(token: str) -> dict:
    """验签并解析 JWT，返回 claims。使用 PyJWT 的 JWKS 验签。"""
    import jwt
    from jwt import PyJWKClient

    try:
        jwks_client = PyJWKClient(OIDC_JWKS_URL)
        signing_key = jwks_client.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=OIDC_AUDIENCE,
            issuer=OIDC_ISSUER,
        )
        return claims
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"令牌校验失败：{e}")


async def get_principal(request: Request) -> Principal:
    """
    FastAPI 依赖：从 Authorization 头提取并校验令牌，
    映射到本系统的成员身份与角色。
    DEV_BYPASS_AUTH=1 时跳过 JWT 校验，返回固定 admin 主体（仅用于本地管道测试）。
    """
    if os.environ.get("DEV_BYPASS_AUTH") == "1":
        import psycopg
        async with await psycopg.AsyncConnection.connect(os.environ["PG_DSN"]) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT id, role FROM member_data.members WHERE role = 'admin' LIMIT 1"
                )
                row = await cur.fetchone()
        if row:
            return Principal(sub="dev-bypass", name="Dev Admin",
                             member_id=str(row[0]), role=row[1])
        raise HTTPException(status_code=500, detail="DEV_BYPASS_AUTH: no admin member found")

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="缺少 Bearer 令牌")
    token = auth[7:]
    claims = await verify_token(token)
    sub = claims.get("sub")
    name = claims.get("name") or claims.get("preferred_username") or "unknown"

    # 查 members 表，定位该登录用户对应的成员记录与角色
    import psycopg
    async with await psycopg.AsyncConnection.connect(os.environ["PG_DSN"]) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id, role FROM member_data.members WHERE oidc_sub = %s",
                (sub,),
            )
            row = await cur.fetchone()

    if not row:
        # 登录成功但未绑定成员记录：拒绝，需管理者先在系统内建立映射
        raise HTTPException(status_code=403, detail="账号未绑定成员档案，请联系管理者")

    return Principal(sub=sub, name=name, member_id=str(row[0]), role=row[1])


def authorize_member_access(principal: Principal, target_member_id: str) -> None:
    """
    分级授权校验。
    admin：可访问任意成员。
    member：仅可访问自己的 member_id。
    违规抛 403。
    """
    if principal.role == "admin":
        return
    if principal.member_id == target_member_id:
        return
    raise HTTPException(status_code=403, detail="无权访问该成员档案")


async def log_access(principal: Principal, action: str, target_member_id: str) -> None:
    """写入访问审计日志。"""
    import psycopg
    async with await psycopg.AsyncConnection.connect(os.environ["PG_DSN"]) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO member_data.access_log
                    (actor_sub, actor_name, action, target_member)
                VALUES (%s, %s, %s, %s)
                """,
                (principal.sub, principal.name, action, target_member_id),
            )
            await conn.commit()
