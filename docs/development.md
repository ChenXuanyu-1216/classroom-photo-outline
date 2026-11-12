# 开发与配置参考

当前安装、测试与发布状态以[源码快速开始](local-quickstart.md)、[README](../README.md)和[发布准备](open-source-release.md)为准。以下历史示例不代表新的验收结果。本地使用 SQLite/文件，显式云模式使用 MySQL/私有 COS。


[项目概览](../README.md) · [文档索引](README.md) · [English](development.en.md)

本页保留安装、界面预览、模型配置、上传契约、测试和数据升级的详细说明。命令中的路径以注明的工作目录为准。真实模型接入仍为原型，质量与性能尚未验收。

## 技术栈

- 后端：Python、FastAPI、Uvicorn、Pydantic。
- 数据存储：SQLAlchemy、SQLite；上传文件保存在本地目录。
- 前端：原生 HTML、CSS、JavaScript，由 FastAPI 提供静态页面，无需 Node.js 构建。
- 模型调用：HTTPX，使用兼容 Chat Completions 的多模态接口。
- 文档导出与测试：python-docx、pytest。

## 快速开始

请使用[当前源码快速开始](local-quickstart.md)，选择自己的密钥与仅回环评审入口，或配置常规微信登录。mock 模型模式不绕过登录。

## 模型与上传配置

编辑 `lecture-syllabus-generator/backend/.env`：

```dotenv
APP_MODEL_PROVIDER=mock
APP_CLUSTER_PROVIDER=rule
APP_MODEL_API_KEY=
APP_MODEL_BASE_URL=https://api.deepseek.com
APP_MODEL_NAME=deepseek-flash
APP_MODEL_TIMEOUT_SECONDS=60
APP_MODEL_TRUST_ENV=false
APP_MODEL_MAX_RETRY=1
APP_MODEL_MAX_TOKENS=4096
APP_ALLOWED_EXT=.jpg,.jpeg,.png,.pdf
APP_MAX_FILE_MB=10
APP_MAX_FILES_PER_BATCH=20
APP_RECOMMENDED_BATCH=10
APP_LOW_CONFIDENCE_THRESHOLD=0.60
```

以上为项目模板的默认值；接入时应按实际服务商填写接口地址和支持视觉输入的模型名称。

模型请求默认直连，不隐式继承 Windows 系统代理或 `HTTP_PROXY` / `HTTPS_PROXY`，避免关闭桌面代理软件后重新打开应用就连接失败。确实需要代理的网络可设置 `APP_MODEL_TRUST_ENV=true` 后重启；识别、归组和知识拓展统一遵守此设置。连接错误的后台日志只记录调用阶段、连接方式与异常类型/系统错误码，不记录密钥或课件内容。

- `mock`：使用预设结果演示本地流程。
- `deepseek`：使用 DeepSeek 官方 `deepseek-flash` 进行图片识别；关闭思考模式，要求结构化 JSON，缺密钥或配置错误直接报错。
- `multimodal`：向 `APP_MODEL_BASE_URL` 下的 `/chat/completions` 发送兼容请求；需支持图片和约定的 JSON 契约，缺密钥直接报错。
- `fallback`：返回识别服务不可用的兜底内容。

网页真实识别与主题归组已接通，配置见 [DeepSeek 网页接入](deepseek-live-application.md)；仓库模板仍默认 `mock`。真实识别设置 `APP_MODEL_PROVIDER=deepseek`，主题归组设置 `APP_CLUSTER_PROVIDER=model`（其他兼容服务使用 `multimodal`）。`.env` 固定读取 `backend/.env` 且已被 Git 忽略，仓库只保留空密钥模板。真实识别读取版本化 `recognize.yaml` 并校验结果；置信度是模型自报的可读性估计，不是校准后的正确率。真实图片接口不接受 PDF，需先逐页转图。

### 按主题聚类并保留原文与来源

`APP_CLUSTER_PROVIDER` 独立控制聚类方式，修改配置后重启后端：

- `rule`（默认）：沿用按首个来源页分组的规则，不调用聚类模型。
- `model`：使用上述 `APP_MODEL_*` 连接配置调用模型，根据知识点内容跨页归组。服务商需要支持 Chat Completions 的文本输入及 `response_format: {"type": "json_object"}`。

真实图片识别和主题聚类同时启用的配置为：

```dotenv
APP_MODEL_PROVIDER=deepseek
APP_CLUSTER_PROVIDER=model
# 同时配置 APP_MODEL_API_KEY、APP_MODEL_BASE_URL 和 APP_MODEL_NAME
```

也可以使用 `APP_MODEL_PROVIDER=mock` 加 `APP_CLUSTER_PROVIDER=model`，单独验证聚类接口；此时聚类输入仍是预设课程内容，不能用来评价真实图片识别质量。模型聚类始终需要有效密钥，不会因识别模式为 `mock` 而免除真实调用。

程序在去重后为本次聚类输入分配 `kp_1`、`kp_2` 等临时 ID，模型只能返回以下结构：

```json
{"modules": [{"title": "索引与查询优化", "point_ids": ["kp_1", "kp_3"]}, {"title": "事务", "point_ids": ["kp_2"]}]}
```

每个输入 ID 必须恰好出现一次。未知、重复或遗漏 ID，空模块、无效标题、额外字段、无效 JSON 和截断结果都会被拒绝。标题为最多 120 个字符的单行文本。程序使用 ID 恢复原始知识点对象，正文、知识点类型、来源类型和全部来源页码均不由模型重写；模块来源页码由程序求并集。同文但不同来源类型的知识点继续分别保留。临时 ID 只对本次聚类有效，不写入数据库，也不作为跨版本引用。

缺密钥、提示词缺失或损坏返回 `CLUSTER_CONFIG_ERROR`；聚类响应无效或服务失败返回 `CLUSTERING_FAILED`，超时返回 `MODEL_TIMEOUT`。仅对超时、连接故障和 HTTP 408、429、5xx 按 `APP_MODEL_MAX_RETRY` 有限重试，格式与 ID 校验失败不自动重试。失败时保留原有大纲和知识点，任务标记失败，不自动降级为规则结果；需要时将配置改为 `rule` 并重启。聚类提示词位于 `backend/prompts/cluster.yaml`，修改后也需重启以刷新缓存。

### AI 知识拓展

真实识别后默认增加一次知识拓展请求，在独立的“AI 知识拓展”章节补充课件外的相关知识，每条标为 `【AI生成】` 并注明“关联课堂页”。设置 `APP_KNOWLEDGE_EXPANSION_ENABLED=false` 可关闭；模拟模式不调用拓展模型。网页、保存与 Word 导出保留标注，失败时保留旧稿。配置、费用、测试命令与能力边界见 [AI 知识拓展](knowledge-expansion.md)。

### 上传与任务创建约定

`POST /api/tasks` 保持 `task_id`、`page_count`、`rejected` 的成功响应结构。允许部分接收：不支持的扩展名、空文件和超限文件进入拒绝列表，有效文件按指定顺序保存，并从 1 开始连续编号；`page_count` 与实际保存的页面数量一致。全部被拒绝时返回 400，不创建空任务。

`order` 为空时采用上传顺序；非空时必须完整包含本批原始上传文件的编号（从 1 开始），每个编号只能出现一次。缺失、重复、越界、非整数和过长参数返回 `INVALID_FILE_ORDER`，不写入任务或文件。被拒绝文件会在排序后过滤，因此有效页面不会出现页码空洞。

业务层按最多 64 KiB 分块读取，并使用实际字节数校验大小。文件先在存储目录内独占暂存、刷新，再整体移动到唯一目录；Task 和 Page 通过一次数据库提交保存。普通读取、写入、发布和数据库失败会回滚并清理本次目录，不清理已有任务文件。新任务目录为 `task-<任务ID>-<随机值>`，旧任务继续按数据库中的 `file_path` 访问，无需迁移文件。

数据库提交报错后会使用独立连接核对：确认已提交则保留文件并返回成功；无法确认时保留文件并返回 `UPLOAD_STATE_UNCERTAIN`（`retryable=false`），应先检查历史记录和服务日志，避免重复上传。清理失败返回 `UPLOAD_CLEANUP_ERROR`，需要维护者核对残留目录；普通保存故障返回可重试的 `UPLOAD_PERSISTENCE_ERROR`。

这些保证覆盖请求处理期间的普通异常。强制结束进程、断电后的残留目录恢复和请求进入业务层之前的传输限流仍未覆盖。上传接收按扩展名与字节数判断；真实识别前新增了实际图片解码、方向、尺寸与元数据处理，见 [AI 核心流程](ai-core-flow.md)。PDF 解码仍未实现。早期上传保证见上传可靠性验证记录（历史资料未随源码分发）。

## 测试

测试覆盖健康信息、上传格式校验、来源标签解析、分阶段识别与生成中的来源保留、跨页去重、编辑保存后重新打开和 Word 内容比对，以及超时、断连、部分失败、重试和异常恢复。

**pytest 会在导入应用之前创建独立临时数据库和上传目录，强制使用模拟识别和默认规则聚类；测试不会清空日常使用的 `backend/data/app.db`。** 每个用例重置临时数据库，整轮结束后清理测试目录。模型识别与聚类测试使用本地模拟传输，不发送真实 API 请求。

聚类测试覆盖跨页分组、完整且唯一的 ID 分配、拒绝模型改写字段、来源保留、有限重试、保存后重新打开和 Word 内容比对，以及重新生成失败时保留旧数据。

在 `backend` 目录安装依赖并执行：

```powershell
# Windows PowerShell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
```

```bash
# macOS / Linux
.venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

首次运行需先按“快速开始”创建虚拟环境。测试默认自行配置隔离环境，不需要修改 `.env`。上述测试不能验证真实模型的识别质量，也不能替代浏览器手工验收。

前端交互逻辑可在仓库根目录使用 Node.js 检查，无需安装 npm 依赖：

```powershell
node --test lecture-syllabus-generator/frontend/tests/*.test.cjs
```

这些用例覆盖记录展示、空状态、网络失败与重试、时间转换、背景调速、进度恢复、编辑草稿保留与结果未知时的只读恢复，不调用真实接口。编辑中禁止导出尚未保存的内容；重新生成的响应或新结果读取失败时，先重新加载当前结果，再开放修改和导出。

安装测试依赖与 Node.js 后，也可从仓库根目录一次运行全部工程检查：

```powershell
pwsh -NoProfile -File lecture-syllabus-generator/scripts/verify_exp03.ps1
```

### 已有数据库升级与来源保留

启动时会为旧 SQLite 数据库的 `page` 表补加可空的 `recognized_structured` 字段，保留原有记录，并可重复启动。新识别结果会将标题、知识点和来源类型一并保存；后续生成和重新生成复用这些数据。`outline` 表另补加可空的 `recognition_provider`，将来源标记与生成版本绑定；旧稿保持未核验。没有保存来源类型的旧记录无法自动恢复真实来源，重新生成时显示“来源待核对”。历史上已保存的大纲不会被批量改写。

相同文字但来源类型不同的条目分开保留；相同文字且来源类型相同的条目才合并来源页码。部分页面失败时仍处理成功页，全部失败时返回明确错误；重新生成失败时保留此前已保存的大纲与知识点。

## V2 单机后台处理

网页模型操作使用持久异步队列，启动方法保持不变，重启后端加载本轮代码。默认 `APP_TASK_QUEUE_ENABLED=true`、`APP_TASK_QUEUE_CAPACITY=20`，无需额外服务；一个数据库仅有一个后台消费者。关闭队列时网页异步请求明确返回 503，旧脚本不带 `Prefer: respond-async` 时仍同步执行。

任务状态增加 `queued`。刷新页面只恢复查询；排队作业重启后继续，已中断的运行作业需要手动重试。生成期间可读和导出旧稿，保存、删除与其他生成会被拒绝，避免覆盖。迁移只增加页错误字段和辅助作业表，保留已有记录。

连接故障使用 0.5 秒起、最多 5 秒的有限退避；超长 `Retry-After` 不提前重试。认证、余额、坏图和无效结构化输出不自动重试。页错误提示区分稍后重试、修正配置和更换图片。实现边界、真实验证与费用见 V2 稳定性专项记录（历史资料未随源码分发）。

超时、空结果和格式异常的错误码、恢复办法、记录保存限制见 [V2 异常记录说明](v2-failure-handling.md)；下一阶段输入、问题台账模板与待补项见 [EXP05 测试输入](exp05-test-inputs.md)。成功重试会清空页错误，实验前后应另存快照。

三个阶段共享独立的 `model_response_parser.py`：空 HTTP body 为 `MODEL_EMPTY_RESPONSE`，正常 completion 空文本为 `MODEL_EMPTY_CONTENT`，其余格式异常保留阶段错误契约。数据模型、解析、业务错误映射与状态落库分别维护；具体修复验证见 响应分类记录（历史资料未随源码分发）。

## 当前限制

- 知识点去重仍使用文本规则；模型聚类只做主题归组，不做语义去重或知识点改写。默认仍使用来源页规则；模型聚类的真实课程质量与性能需要实测。
- 真实识别使用校验后的 JSON，代码与公式正文保留；可读性置信度由模型自报，不是校准正确率。答案与归组质量需人工核对。
- 模拟上传模板包含 PDF，但真实图片接口不接收 PDF，也未实现多页 PDF 拆页；请先转为 JPG、JPEG 或 PNG，并同步配置允许格式。
- 日常启动默认监听 `0.0.0.0:8000`；电脑仍可用 `127.0.0.1`，手机使用电脑当前局域网 IP。反代部署设置 `APP_SERVER_HOST=127.0.0.1`。业务接口已启用微信会话与任务归属校验；网页使用微信扫码登录，配置和旧资料认领见[登录说明](wechat-auth.md)。公网 HTTPS、配额及运维验收尚未完成。
