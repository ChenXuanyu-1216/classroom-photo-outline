# 微信云托管部署与验收

网页与小程序共用 FastAPI、账号归属和资料。云配置使用 MySQL 保存账号、任务、大纲、队列及预算，私有 COS 保存原图、上传分块和临时 Word；本机开发仍使用 SQLite 和本地文件。

本分支提供部署代码，尚未在真实云环境发布。电脑上的开发服务、密钥和真实数据库未被迁移。CI 的临时 MySQL、模拟 COS 和镜像启动检查不能证明云端权限、后台 CPU 或真实模型可用。

## 构建与资源边界

构建上下文为仓库根目录，Dockerfile 为根目录的 Dockerfile。端口 8000，可用 PORT 改写；镜像以 UID 10001 运行，排除 .env、数据库、storage、令牌和测试资料。

- API 命令：python run.py。云模式只处理 HTTP，不启动嵌入式消费者，不自动建表。
- Worker 命令：python -m app.worker。与 API 使用同一镜像、MySQL、私有桶、密钥及预算；健康端口只提供健康检查。
- 第二个执行实例可能消耗额外资源。先确认所选“微信云托管”产品及计费模式支持无业务请求时持续执行，再确定 worker 承载方式。不得用手机轮询、默认最小实例数或其他 CloudBase 产品的文档代替验证。若不支持，先调整为平台支持的持久执行器。
- 小程序通过绑定环境的 callContainer 访问私有服务；公网网站入口另行开放。不要为下载原图把 COS 桶设为公共读。

参考：[小程序访问](https://docs.cloudbase.net/run/develop/access/mini)、[服务开发约束](https://docs.cloudbase.net/run/develop/developing-guide)、[公网访问](https://docs.cloudbase.net/run/deploy/networking/public)。需按实际产品、账号和控制台核实规则及费用。

## 配置与首次初始化

模板在 backend/.env.cloud.example。仅在云运行环境注入真实值，不提交密钥、不上传本机 .env。

1. 确认实际环境 ID（不是名称 prod）、服务 kezhao-api、小程序与环境的绑定。未填真实环境 ID 时，体验版/正式版拒绝回退到局域网。
2. 准备专用 MySQL 及最小权限账号，按网络配置 TLS CA。测试只读取 APP_TEST_MYSQL_URL 且限制数据库名为 kezhao_test_ci，不能指向真实数据库。
3. 准备私有 COS 桶及临时凭据，按下节选择实际平台支持的凭据来源；不要授予账户管理员权限。
4. 运行 python -m app.manage migrate-cloud。cloud-0001 是首次建库版本，拒绝接管已有非空、无版本库。以后升级必须新增迁移，不能用反复 create_all 代替。
5. 运行 python -m app.manage check-private-bucket，只输出结果，不修改权限。桶 ACL 须仅授予所有者；桶策略含 Allow 或无法读取时要求人工审查。另查角色权限、存量对象 ACL，不能仅凭单个对象 ACL 判定桶私有。
6. 核对旧模型账本预留额，运行 python -m app.manage initialize-cloud-budget --reserved-fen 金额。单位为分，已有账本不能重置为零。预留估算与账单不同；非 DeepSeek 也预留，但其费率仍需核实。

/health/live 检查进程，/health/ready 检查数据库和表版本；均不代表模型、COS 和后台执行已经验收。

### 微信云托管原生存储凭据

在「服务管理 → 云调用」开启开放接口服务，并只授权 `/_/cos/getauth`。云运行环境填写：

```dotenv
APP_COS_CREDENTIALS_SOURCE=wechat-cloudrun
APP_COS_BUCKET=实际环境的存储桶名称
APP_COS_REGION=ap-shanghai
```

将 APP_COS_SECRET_ID、APP_COS_SECRET_KEY、APP_COS_SESSION_TOKEN、APP_COS_CREDENTIALS_FILE 留空。后端通过平台内部固定 HTTP 地址取得当前环境的临时凭据，不发送 AppSecret 或账户永久密钥。提前 60 秒刷新，并发请求共用一次刷新；过期、权限拒绝、超时、非法响应或重定向返回脱敏的 503，不回退到静态或旧凭据。此模式只允许 cloud/COS 运行配置，不能把内部地址当作本机公网凭据服务。

对象存储的「所有用户不可读写」限制微信客户端通道；后端仍可通过管理员形态的 COS SDK 操作。当前小程序和网页都通过带会话、归属校验的后端分块接口取文件，不使用原生存储 SDK 或公开 tcb.qcloud.la 链接，因此暂不需要客户端文件元数据编码权限。COS 对象写入仍显式 private；上线前还须核对底层桶 ACL/策略和客户端通道，两者不能相互替代。

平台临时角色可访问当前环境存储桶；应用限制键前缀为 kezhao/，但这不等于角色已被 IAM 限制到此前缀。使用专用环境，并保留实际角色、桶权限及凭据轮换验收。参考[微信官方 COS-SDK 服务端说明](https://developers.weixin.qq.com/miniprogram/dev/wxcloudservice/wxcloudrun/src/development/storage/service/cos-sdk.html)。

其他部署采用默认 `APP_COS_CREDENTIALS_SOURCE=configured`：可用 APP_COS_CREDENTIALS_FILE 投射 JSON（secret_id、secret_key、token、expires_at，Unix 秒），每次使用检查过期并重载；未配置文件时才读取显式环境凭据。投射文件如何由部署平台轮换须另外配置，不能只填一个会过期的 token。

### 启动时数据库检查失败

旧提示 `Cloud schema is unavailable; run the explicit migration` 同时掩盖了连接、认证、授权和库名错误，不能据此推断必须重新建表。已初始化的测试库应保留，先看部署日志中的脱敏分类：

| 分类 | 排查方向 |
| --- | --- |
| MYSQL_AUTH_FAILED:1045 | 运行账号、密码及允许的客户端主机；不能仅凭此码断言密码错误 |
| MYSQL_DATABASE_DENIED:1044 / MYSQL_PERMISSION_DENIED:1142 | 应用账号对目标库和表的权限 |
| MYSQL_DATABASE_NOT_FOUND:1049 | 实际环境变量中的库名 |
| MYSQL_TABLE_NOT_FOUND:1146 | 实际选择的库及显式初始化结果 |
| MYSQL_CONNECT_FAILED:2003 / MYSQL_HOST_UNRESOLVED:2005 | 服务与数据库的环境、内网、地址、端口或 DNS |
| MYSQL_CONNECTION_LOST:2013 / MYSQL_TLS_FAILED:2026 | 数据库可用性、网络或 TLS 配置 |
| DB_CHECK_FAILED | 未识别的数据库检查异常；继续采集受控诊断，不打印原始连接串或驱动正文 |

分类只输出固定提示和白名单错误码，不输出密码、地址、SQL 参数或上游异常正文。匿名就绪接口仍只返回 unavailable，不增加诊断接口、自动重试、建表权限或存储回退。修复日志不等于修复实际云连接；必须在云端重新验证。

### 就绪成功但微信登录返回 WECHAT_UNAVAILABLE

`/health/ready` 只检查 MySQL 和表版本，不会验证后端到微信 API 的 HTTPS 链路。开放接口服务会在容器中注入 `/app/cert/certificate.crt`；HTTPX 默认的 certifi CA 包不自动读取该证书，配置 `REQUESTS_CA_BUNDLE` 也不等于 HTTPX 已加载它。

cloud 配置下，微信登录/二维码共用的客户端会在默认 certifi 信任链上追加该固定文件，保留 HTTPS、服务端域名校验、超时和禁止重定向。仅这个微信客户端使用追加后的信任链；本机模式和模型客户端不改变。缺少平台文件时保留默认 TLS 校验；证书存在但不可读取/解析时返回脱敏的 `WECHAT_TLS_CONFIG_ERROR`，不回退到 `verify=False` 或 HTTP。

更换代码包后重新发布，保留已验证的 MySQL、AppID/AppSecret、私有存储及 mock 测试配置；无须重新建表、重置账号或将 AppSecret 放入小程序。重新验证原生 callContainer 的就绪和微信登录，再验收私有文件。只通过本机合成 TLS 测试不代表真实云端登录已经恢复。参考[微信官方开放接口服务的 HTTPS 说明](https://developers.weixin.qq.com/miniprogram/dev/wxcloudservice/wxcloudrun/src/guide/weixin/open.html)。

## 无付费调用的后台探针

隔离配置使用 APP_MODEL_PROVIDER=mock、APP_CLOUD_ALLOW_MOCK=true，保留 APP_CLOUD_EXECUTION_VERIFIED=false。

候选 worker 命令为 python -m app.worker --probe-seconds 900。日志输出 probe_id，数据库保存心跳、最大间隔和完成状态。关闭小程序、停止业务请求，等待测试时段，再运行 python -m app.manage probe-status --id 探针编号。

核对持续心跳、最大间隔、资源及重启行为，另测滚动发布和失去协调连接后的接管。中断的探针不得算完成。探针无模型调用，不会自动启用真实模型。只有实际产品约束、运行观测及重启测试通过后，才人工启用模型及 APP_CLOUD_EXECUTION_VERIFIED=true。失败时先更换受支持执行方案。

## 迁移、备份与回退

维护命令在 backend 目录运行，所有路径须显式指定。先停止源写入和目标 API/worker，备份源 SQLite 及整个原图目录。数据库备份可能含旧登录信息，须私密保存，不提交 Git。

    python -m app.manage backup-local --source 源SQLite路径 --output 新备份路径
    python -m app.manage cloud-inventory --source 新备份路径 --source-storage 原图目录
    python -m app.manage cloud-import --source 新备份路径 --source-storage 原图目录 --expected-fingerprint 盘点指纹
    python -m app.manage cloud-verify --source 新备份路径 --source-storage 原图目录 --expected-fingerprint 盘点指纹

盘点只读，输出数量、指纹、原图总字节和缺失页面编号，无 openid、令牌或正文。导入只接受空业务库，保留账号、归属、识别资料和已保存大纲；排除会话、网页登录挑战和作业。未完成任务标为中断，重新登录后由用户决定是否重试。

原图先私有复制并校验 SHA-256，再提交资料。SQL/COS 无法组成原子事务，失败副本保留持久清理记录；结果不确定时先核对目标，不能盲目重跑或删除。未清理副本会占额度，重新导入前须清理并复核空库。

原图缺失默认阻止导入。审核清单后可显式加 --allow-missing-originals，保留文字、大纲并标记缺失；不能从大纲恢复图片，也不能自动认领无归属记录。

先恢复到另一个空测试库并运行 cloud-verify，核对资料和对象，确认旧会话无效。投入使用后须做 MySQL 与对象成套备份/恢复演练。版本回退前保留云端增量，不能把旧本机库直接覆盖上线后的资料。

## 文件、费用与放行

上传/下载均使用带会话的 256 KiB 分块；同一上传意图可重试而不重复建任务。文件句柄绑定账号与任务，15 分钟过期，无公共下载链接。

默认单批 8 MiB/20 张，用户 512 MiB/200 任务，全局 2 GiB/2000 任务；未完成上传用户 3/全局 128，用户进行中作业 3。分块增加 API/COS 请求数，需真机测量。删除提交后排队清理，失败保留记录重试，仍被其他任务引用的对象不删除。

应用额度不能限制云账单总额。CPU、内存、MySQL、构建、流量、模型须分别配置预算、告警和到期处理；试用不等于长期免费。

默认 APP_PUBLIC_WEB_ENABLED=false。网站入口确认后设 true，APP_PUBLIC_ORIGIN 设唯一 HTTPS 源（无路径/尾斜杠）。云扫码创建、轮询、取消比较固定 Origin，不信任任意 Host/X-Forwarded-*；Cookie 始终 Secure。

上线仍需真实环境 ID、MySQL/私有 COS 权限、独立后台执行、恢复演练、费用边界、非开发者真机验收、备案/隐私/代码审核。至少两个账号验证上传、识别、保存、原图、Word、断线恢复与跨账号拒绝。开发者调试成功不能写成所有微信用户可用。


## Evaluation schema update

The reconciled source uses schema revision `cloud-0002`. Before deploying this code over a `cloud-0001` database, stop API/workers, take verified backups, and run `python -m app.manage migrate-cloud` from the backend with the intended operator configuration. This explicitly adds the evaluation tables; startup stays fail-closed until the schema matches. See [source reconciliation](source-reconciliation.md) for data-import compatibility and verification limits.
