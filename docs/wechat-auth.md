# 微信登录与任务归属

所有 `/api/tasks` 接口现在必须携带本系统 Bearer 令牌，任务、原图、作业、大纲、生成、重试、导出与删除均校验当前用户归属。别人的任务、未归属旧任务和不存在的任务统一返回 404。没有匿名本机访问开关。

## 配置与使用

后端 `.env`（已被 Git 忽略）配置 `APP_WECHAT_APP_ID` 和 `APP_WECHAT_APP_SECRET`，AppID 必须与导入工程相同；AppSecret 不放入小程序源码。重启后端，在小程序首页点击“微信登录”；后续业务请求会复用七天有效的后端会话。也可以在首次访问自己的记录时自动完成登录。无需手机号、头像或昵称权限。

流程为 `wx.login` → 临时 code 发往本项目后端 → 后端固定微信 `code2Session` 接口验证 → 返回自己的随机令牌和内部用户 ID。客户端不提交可信 owner_id/openid。微信 session_key 不落库、不回传；后端会话只存令牌摘要，支持注销。微信 HTTP 请求日志和异常均对敏感凭证脱敏。

客户端令牌按后端地址存储，草稿按地址和用户 ID 分区。原图/Word 使用带 Authorization 的下载请求，令牌不出现在 URL。401 清理会话，不自动重发生成、保存或删除请求；用户重新操作后才会尝试登录。

## 旧记录

迁移增加 owner_id 和用户/会话表，不删除旧记录。默认旧任务保持未归属，也不会自动归给首个登录者。由掌握服务器文件权限的维护者在后端目录显式操作：

```powershell
.\.venv\Scripts\python.exe -m app.manage list-users
.\.venv\Scripts\python.exe -m app.manage claim-legacy --user-id 1 --task-ids 2 3
```

需要认领所有未归属记录时使用 `--all-unassigned`。命令不允许抢占已经属于其他用户的任务，没有对外的管理接口。

本次用户明确授权后，已把备份中的 13 条未归属旧任务分配给当前唯一验证登录的微信用户。操作前备份位于后端 `data/before-auth-*.db`，未删除照片、大纲或历史内容。

## 网页版

访问 `/login.html`，用手机微信扫描网页显示的小程序码，核对两端六位校验码，在小程序中点击“确认登录”。网页会进入同一个微信账号，直接访问该账号已有照片、大纲和整理记录，不创建独立本地用户，也不迁移任务。首页导航提供“微信扫码登录”，访问需要登录的业务页时自动转到登录页，成功后返回原站内页面。

网页二维码使用微信 `stable_token` 和 `getwxacodeunlimit` 服务端接口生成；二维码内只有随机 scene，AppSecret 和微信 access_token 不回传浏览器。浏览器另持有独立的 HttpOnly cookie 证明，其他浏览器不能领取登录结果；多标签页证明互不覆盖。扫码先绑定微信用户，明确确认后，原浏览器才能一次性兑换七天会话。二维码五分钟失效，支持取消、拒绝和刷新；网页会话存在 sessionStorage，退出只撤销此网页会话，不退出手机。

正常网页登录已移除命令行生成令牌和粘贴入口。维护者 CLI 仍只用于服务器维护，不能用于关联微信账号。

### 开发联调与正式部署

- 后端配置 `APP_WECHAT_QRCODE_ENV=develop`、`trial` 或 `release`；当前本机配置为 `develop`，正式部署改为 `release`。开发版/体验版需包含 `pages/web-login/web-login` 页面，扫码微信号需具有相应版本访问权限。
- 手机必须访问同一个后端。电脑的 `127.0.0.1:8000` 仅能在电脑访问；不能作为手机后端地址。真机联调需可访问的局域网开发服务或 HTTPS 服务，并按微信开发/体验环境要求配置域名校验；正式版使用 HTTPS 合法域名。
- 后端出口 IP 若被微信限制，需在小程序后台配置 IP 白名单；相关失败只显示脱敏错误，不输出 AppSecret。
- HTTPS 反向代理必须保留公开 Host，并传递 `X-Forwarded-Proto: https`。后端只信任实际代理 IP，例如同机 Nginx 使用 `uvicorn app.main:app --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips 127.0.0.1`，Nginx 设置 `proxy_set_header Host $host;`、`proxy_set_header X-Forwarded-Proto $scheme;`、`proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;`。不使用任意来源通配信任。这样 Origin 校验、Secure cookie 和按来源 IP 限流会按公开 HTTPS 地址及真实客户端工作。
- 当前创建登录码限制为每来源 IP 每分钟 5 次、全局每分钟 120 次（数据库记录，限流统计在调用微信前完成）；部署仍需入口层限流，以严格控制并发和共享出口下的滥用。

## 已验证与边界

隔离测试覆盖未登录、伪造身份、两个用户跨对象访问、过期/注销、秘密脱敏、迁移与显式认领。真实微信登录已在开发者工具成功；用户随后确认手机登录成功并能读取记录。新照片全链路仍待真机复测，本轮未新增付费模型识别。后端仍需正式 HTTPS 部署、平台域名配置与额度限制后才能对外运营。
