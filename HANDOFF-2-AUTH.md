# HANDOFF-2 — 认证闭环接续说明

承接 HANDOFF.md。核心管道已验证跑通（4 条规则命中 + RAG 检索 + 中文报告输出）。
本文件指导完成最后一公里：接上 Authentik 认证，让链路从登录到评估完整走通。

当前临时状态：.env 里 DEV_BYPASS_AUTH=1，/assess 端点无鉴权（仅限本地测试，
完成本文件后必须去掉）。

三步有先后依赖，顺序不可乱。第 1 步是浏览器图形操作，须由人工完成；
第 2、3 步可由 Claude Code 执行。

---

## 第 1 步（人工操作）：在 Authentik 创建 OIDC Application + Provider

打开浏览器访问 http://localhost:9100，用管理员账号登录
（首次走 http://localhost:9100/if/flow/initial-setup/ 设置 admin 密码）。

进入 Admin Interface：

1. Applications → Providers → Create → "OAuth2/OpenID Provider"
   - Name: virtual-hospital-provider
   - Authorization flow: 默认 implicit consent
   - Client type: Public
   - Client ID: 记下生成值
   - Redirect URIs: http://localhost:5500/ 和 http://127.0.0.1:5500/
     （与前端托管端口一致）
   - Signing Key: 默认自签证书
   - 保存

2. Applications → Applications → Create
   - Name: Virtual Hospital
   - Slug: virtual-hospital
   - Provider: 选上一步的 virtual-hospital-provider
   - 保存

3. 记下三个值（第 2 步用）：
   - Client ID（Provider 详情页）
   - Issuer: http://localhost:9100/application/o/virtual-hospital/
   - JWKS URL: http://localhost:9100/application/o/virtual-hospital/jwks/

4. Directory → Users，确认有代表成员A的用户，记下其标识。
   真实 sub 值以第 3 步登录拿到的 token 解码为准。

把 Client ID / Issuer / JWKS URL / 用户标识 交给 Claude Code 做第 2 步。

---

## 第 2 步（Claude Code 指令）：回填配置 + 绑定 oidc_sub

将下面整段交给 Claude Code 执行：

```
请完成 virtual-hospital 的 OIDC 配置回填，参数如下（我从 Authentik 拿到的）：
- Client ID: <填入>
- Issuer: <填入>
- JWKS URL: <填入>

执行：
1. 把这三个值填入 orchestrator 的环境变量（docker-compose.yml 或 .env）：
   OIDC_ISSUER / OIDC_JWKS_URL / OIDC_AUDIENCE(=Client ID)
   关键：容器内访问 Authentik 必须用容器网络地址
   http://authentik-server:9000/application/o/virtual-hospital/...
   而不是 localhost:9100。jwks 验签是容器内部发起的。
   浏览器侧（前端）才用 localhost:9100。这个内外地址区分务必处理对。
2. 绑定成员A的 oidc_sub（sub 值若暂不确定，先留空，第 3 步登录后解码 token 再回填）：
   UPDATE member_data.members SET oidc_sub = '<sub值>'
   WHERE id = '<MEMBER_UUID>';
3. docker compose up -d orchestrator 重启使配置生效。
完成后报告。
```

---

## 第 3 步（Claude Code 指令）：前端接真实认证 + 去掉 bypass

将下面整段交给 Claude Code 执行：

```
请完成 virtual-hospital 前端接真实认证并移除测试旁路：
1. 编辑 frontend/index.html：
   - USE_MOCK 改为 false
   - OIDC.authority = http://localhost:9100/application/o/virtual-hospital/
   - OIDC.clientId = <第1步的 Client ID>
   - OIDC.redirectUri 与 Authentik 里填的 Redirect URI 一致
2. 托管前端：cd frontend && python3 -m http.server 5500
3. 暂不要动 DEV_BYPASS_AUTH，等我先手动登录验证一次。
报告托管地址，等我浏览器验证。
```

人工验证（你在浏览器做）：
访问 http://localhost:5500 → 点登录 → 跳 Authentik → 输成员A账号密码
→ 跳回前端 → 看到档案 → 运行评估 → 出中文报告。

若登录后报 403「账号未绑定成员档案」：token 的 sub 与库里 oidc_sub 不一致。
让 Claude Code 解码 token 看真实 sub，回第 2 步更新 SQL，重启 orchestrator，再试。

验证通过后，最后交给 Claude Code：

```
登录评估已验证通过。请移除测试旁路：
1. 从 .env 删除 DEV_BYPASS_AUTH（或设为 0）
2. docker compose up -d orchestrator
3. 验证：未登录直接 curl http://localhost:8000/assess 应返回 401。
报告结果。
```

---

## 完整路径闭环的标志

- 未登录访问 /assess → 401
- 浏览器登录 Authentik → 跳回前端成功
- 前端显示成员A档案
- 运行评估 → 中文报告 + 国际指南引用 + 规则命中
- .env 中 DEV_BYPASS_AUTH 已移除

达成即为认证→授权→评估→报告整条链路闭环。

---

## 注意事项

- 容器内用 authentik-server:9000，浏览器用 localhost:9100。混用是本阶段最常见失败原因。
- 隐式流仅用于本地验证，日后对外改授权码+PKCE。
- 约束 A/B 本阶段不涉及。
- 环境问题（健康检查、证书、CORS）由 Claude Code 迭代排查；设计取舍回原对话。
