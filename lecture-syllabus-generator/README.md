# Application source

FastAPI 后端和原生 HTML/CSS/JavaScript 网页。项目入口见[中文 README](../README.md) / [English](../README.en.md)。安装及两种认证入口见[本地快速开始](../docs/local-quickstart.md)。

- `backend/`：接口、模型适配、提示词、数据库、常规 `run.py` 和仅回环 `reviewer.py`。
- `frontend/`：登录、上传、进度、大纲、历史与评价工作区。
- `scripts/`：开发启动、内容回归和质量/可靠性检查；真实调用需自行确认权限与费用。

普通 `run.py` 的 mock 模型模式不取消微信登录。源码评审使用私有 `.env.reviewer` 和自己的 DeepSeek 密钥，不依赖私有 ZIP。测试与限制见[测试说明](../README.md#测试)和[开发参考](../docs/development.md)。
