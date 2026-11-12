# DeepSeek 网页识别 / Live web recognition

网页已支持真实照片识别和按主题生成复习框架；仓库模板仍默认模拟模式，防止未配置时产生费用。合成图片已跑通网页与 Word 导出，真实课堂质量仍需对照照片人工校对。

真实调用前现会校验和处理图片，整理带来源的本次任务上下文，再统一校验模型输出。配置与边界见 [AI 核心流程](ai-core-flow.md)。升级后先安装更新的 `requirements.txt`，再重启后端。

## 配置与使用

在本地 `lecture-syllabus-generator/backend/.env` 配置以下内容，密钥只保存在该忽略文件中。已有密钥时保留原值，不要覆盖整个文件。

```dotenv
APP_MODEL_PROVIDER=deepseek
APP_CLUSTER_PROVIDER=model
APP_MODEL_API_KEY=你的本地密钥
APP_MODEL_BASE_URL=https://api.deepseek.com
APP_MODEL_NAME=deepseek-flash
APP_MODEL_TIMEOUT_SECONDS=60
APP_MODEL_MAX_RETRY=0
APP_MODEL_MAX_TOKENS=4096
APP_MODEL_BUDGET_CNY=5
APP_MODEL_BUDGET_ENFORCED=false
APP_ALLOWED_EXT=.jpg,.jpeg,.png
```

从 `backend` 目录运行 `.venv/Scripts/python.exe run.py`，打开 [上传首页](http://127.0.0.1:8000/)。修改配置后重启后端；不要同时为同一数据目录启动多个服务。端口占用时先确认原服务，避免误停其他项目。

1. 确认页面显示“DeepSeek 真实识别”，添加 JPG/JPEG/PNG，调整顺序后生成大纲。提交会将本次照片发送到 DeepSeek 官方接口。
2. 内容识别保留代码、公式和来源页；图中明确问题在条件充分时生成标记为“AI推断”的答案。看不清或缺少条件时显示待核对项，不承诺每题都能作答。
3. 主题归组使用识别出的知识点 ID；原文与来源由程序恢复。完成后可编辑、保存、重开与导出 Word。

旧模拟结果不会仅因切换配置变成真实识别结果。新生成的大纲保存识别来源标记；旧记录无法确认时显示未核验。重新生成可能再次调用模型并覆盖当前稿件，先确认需要再操作。

## 重启后的检查与费用

首页读取本地 `/api/meta`，检查配置是否就绪与本地剩余额度，不调用付费模型，也不验证密钥在云端是否有效。检查最多等待 8 秒；后端未启动、请求超时、配置未就绪或额度不足时可点“重新检查服务”，已选照片和顺序保留。若原窗口仍使用旧资源，刷新一次页面；页面和脚本会重新校验缓存，配置接口不使用缓存。

网页识别、主题归组、连接测试脚本共用 `backend/data/deepseek-test-budget.json`。每次尝试前按请求体和输出上限估算，至少记录 0.10 元保守预留；失败或超时仍保留。当前 `APP_MODEL_BUDGET_ENFORCED=false`，不因超过 5 元预留而拒绝调用；网页显示“未设置本地额度上限”。只有显式启用该开关时 `APP_MODEL_BUDGET_CNY` 才生效。历史用量不清零；记录损坏或锁被占用仍须核对。

该额度是依据已核实价格计算的项目本地保守预留，**不是官方账单，也不能限制其他工具或项目副本的消费**。实际金额查看官方控制台；价格变化时需重新核实。认证失败和余额不足不自动重试，错误不会显示密钥或服务商原始响应。

此前公开课堂数据集没有因本功能启用而自动上传云端；本轮代理验收仅使用合成图。用户自行选择的网页上传照片按本次提交处理。

## English

Set `APP_MODEL_PROVIDER=deepseek` and `APP_CLUSTER_PROVIDER=model` in ignored `backend/.env`, retaining your local key. Use the official endpoint and `deepseek-flash`, set the project allowance to CNY 5 and allow JPG/JPEG/PNG, then restart the backend and open the upload page. The repository template remains in mock mode.

Submitted photos are sent to DeepSeek for structured recognition, followed by topic grouping. Code, formulas and page references are retained; inferred answers and unreadable regions are marked for review. Synthetic web recognition and Word export have been verified, but classroom accuracy has not been accepted.

The homepage configuration check is local and free. It ends within eight seconds and offers retry while preserving selected photos. Frontend assets revalidate their cache; metadata bypasses it. Recognition, grouping and the connection probe share a persistent conservative allowance, reserving at least CNY 0.10 per attempt. Failed attempts keep reservations. This is neither the official bill nor an account-wide spending limit. Do not delete the ledger to continue spending.
