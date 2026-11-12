# DeepSeek Flash 连接测试 / Connection test

本文保留独立连接测试的方法；连接阶段使用 `mock`，随后已启用[真实网页识别](deepseek-live-application.md)。脚本仅发送固定文字与合成图，不运行课堂照片评测；与网页共用保守用量记录。本机额度开关由运行者自行配置；应用预留估算不替代提供者账单或计费限制。

## 本地配置

注册 [DeepSeek 官方开放平台](https://platform.deepseek.com/)，确认余额，再创建专用于此项目的 API-Key。充值金额与实际调用费用不同，最低充值要求以控制台为准。密钥只填本地 `lecture-syllabus-generator/backend/.env` 的 `APP_MODEL_API_KEY`，不要放入聊天、前端、截图或 Git 提交。

```dotenv
APP_MODEL_PROVIDER=mock
APP_CLUSTER_PROVIDER=rule
APP_MODEL_API_KEY=
APP_MODEL_BASE_URL=https://api.deepseek.com
APP_MODEL_NAME=deepseek-flash
APP_MODEL_TIMEOUT_SECONDS=60
APP_MODEL_MAX_RETRY=0
APP_MODEL_MAX_TOKENS=4096
APP_MODEL_BUDGET_ENFORCED=false
APP_MODEL_BUDGET_CNY=5
```

`.env` 已被 Git 忽略，固定从 `backend/.env` 读取。修改后需重启应用。源码默认使用 `deepseek-flash`；运行前按[提供者文档](https://api-docs.deepseek.com/zh-cn/)和自己的账户核对模型可用性与价格，不将名称视为固定历史快照。

## Windows 测试命令

从仓库根目录执行。先检查配置，本条命令不发网络请求、不扣费：

```powershell
.\lecture-syllabus-generator\backend\.venv\Scripts\python.exe lecture-syllabus-generator/scripts/check_deepseek.py
```

输出只包含密钥是否存在。`missing_key_not_called` 表示仍需填写；`configured_not_called` 不能证明密钥有效。

明确调用一次固定文字 JSON：

```powershell
.\lecture-syllabus-generator\backend\.venv\Scripts\python.exe lecture-syllabus-generator/scripts/check_deepseek.py --live
```

一次文字通过后，再调用一次合成图；单次执行最多2个请求，无自动重试：

```powershell
.\lecture-syllabus-generator\backend\.venv\Scripts\python.exe lecture-syllabus-generator/scripts/check_deepseek.py --live --vision
```

合成图固定为 `backend/tests/fixtures/deepseek-synthetic-slide.png`，含 Vue 代码和 `P(y|x)`，不含课堂照片或个人资料。脚本只允许官方 DeepSeek 地址及 `deepseek-flash`，校验 JSON 及图中公式、代码的原样保留，不扫描桌面或课堂数据集。

## 费用与记录

- 脚本按源码中的保守参数估算预留，实际价格和账单以提供者为准；运行前核对当前条款。
- 每次尝试前按请求规模记录至少0.10元保守预留，失败、超时、无效内容也保留记录。`APP_MODEL_BUDGET_ENFORCED=false` 时不设本地金额上限；只有显式设为 `true` 时，`APP_MODEL_BUDGET_CNY` 才作为本地累计上限生效。
- 固定文字输出上限128 Token，图片输出上限4096 Token；图片提示词少于9000 UTF-8字节。关闭思考模式和自动重试。
- 预算记录为 `backend/data/deepseek-test-budget.json`；最近成功检查及用量为同目录 `deepseek-last-check.json`，均不提交 Git。预算损坏或锁被占用时停止，不自动重置。不要删除预算文件以继续调用。
- 预留金额不是实际扣费或账户余额。关闭限制后仍保留历史累计值，网页显示“未设置本地额度上限”，实际费用以官方账单为准；官方涨价时需重新核实估算，不能沿用旧价格假设。

401 表示密钥认证失败；402 表示余额不足，均停止测试、不自动重试、不显示服务商原始响应或密钥。

## 后续启用边界

独立探针不要求启用网页真实识别。网页启用时将 `APP_MODEL_PROVIDER` 改为 `deepseek` 并重启。该模式缺密钥直接报错，不退回模拟结果；支持 JPG/JPEG/PNG，PDF 需先逐页转图。模型自报可读性置信度不是经过人工校准的正确率。

`APP_CLUSTER_PROVIDER=rule` 保持来源页归组；模型归组是独立付费步骤。此前课堂数据集不会自动上传；用户自行网页提交的图片会发送至配置的模型服务。正式课堂质量评测仍待完成。合成图接通不代表真实课堂质量验收通过。

## English

This is a connection test, not a classroom benchmark. The template defaults to `mock` and `rule`; live web setup is documented separately in [the live guide](deepseek-live-application.md#english). Put the key only in ignored `backend/.env`. The script defaults to a local configuration check with zero API requests; `--live` sends one fixed text request and `--live --vision` adds one fixed synthetic slide, with no retries or classroom-photo uploads.

Each attempt records at least CNY 0.10 as a conservative estimate before sending; failed attempts retain their records. The local monetary cap is disabled by default (`APP_MODEL_BUDGET_ENFORCED=false`); only an explicit `true` enables the configured cap. Historical estimates are retained and do not represent actual account spending or balance. Results expose only safe metadata and token usage. A successful synthetic test proves connection and basic structured/image handling, not real classroom quality.
