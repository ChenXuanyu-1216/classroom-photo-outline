# 课堂 PPT 照片智能大纲生成器

[English](README.en.md) · [文档导航](docs/README.md) · [MIT 许可证](LICENSE)

将课件照片整理为可编辑、带来源页码的复习大纲，支持 Word 导出。本项目提供 FastAPI 后端、原生网页与微信小程序源码，定位为开发者预览版。当前交付为源码，不提供公共托管服务或 Windows 安装程序。

## 功能

- 按顺序上传、识别、跨页归组、大纲校对保存、历史记录、原图查看与 Word 导出。
- 按用户隔离的评价快照、追加人工评价及 JSON/Markdown 证据导出。
- 使用运行者自己 DeepSeek 密钥的本机浏览器评审入口，无需配置微信。
- 常规网页与小程序入口使用微信服务端登录校验和任务归属检查。
- 本地 SQLite/文件存储，以及显式配置的 MySQL/私有 COS 云模式、独立后台执行与迁移工具。

## 本地启动

需要 Python 3.10+ 和 pip；离线 JavaScript 测试使用 Node.js 24。网页不需要 npm 安装或前端构建。

从仓库根目录进入后端，安装依赖：

```powershell
cd lecture-syllabus-generator/backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
```

macOS/Linux 使用 `python3 -m venv .venv`，再运行 `.venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt`。

按照[本地快速开始](docs/local-quickstart.md)选择入口：

| 入口 | 需要准备 | 范围 |
| --- | --- | --- |
| 源码本机评审 | 自己的 DeepSeek 密钥及私有 `.env.reviewer` | 仅回环地址的网页流程；真实识别可能产生费用 |
| 常规应用 | 自己的微信 AppID/AppSecret 与已配置客户端 | 网页扫码、小程序；mock 模型模式仍需要登录 |
| 界面预览 | 打开 [frontend-preview.html](frontend-preview.html) | 静态示例界面，不识别图片、不提交任务 |

可复用输入为 `lecture-syllabus-generator/backend/tests/fixtures/deepseek-synthetic-slide.png`。它是合成样例，不能证明课堂识别准确率。源码不分发真实课堂照片数据集。

## 测试

完整后端验收包含 Git 版本取证测试，需要 Git 检出且已有至少一个提交。新复制但未初始化 Git 的目录可先运行 `pytest -q tests/test_smoke.py`；本机评审不要求 Git。

安装依赖后，在后端目录运行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

在仓库根目录运行：

```powershell
node --test lecture-syllabus-generator/frontend/tests/*.test.cjs
node --test miniprogram/tests/*.test.cjs
.\lecture-syllabus-generator\backend\.venv\Scripts\python.exe -m pytest scripts/tests/test_reviewer_runtime.py -q
```

macOS/Linux 将 Python 路径替换为 `lecture-syllabus-generator/backend/.venv/bin/python`。测试使用临时数据库与模拟上游；真实 MySQL 检查需要独立测试服务。验证范围与剩余事项见[源码发布状态](docs/open-source-release.md)。历史通过数不代表当前修改后的源码已重新验收。

## 配置与限制

- 从空配置示例开始填写自己的参数。小程序 AppID、云环境 ID 和服务名称默认留空，见[小程序说明](miniprogram/README.md)。
- 模型密钥和微信 AppSecret 仅保存在后端。不要提交私有配置、会话、照片、运行数据库或私有打包产物。
- mock 返回预设内容；真实识别需要配置模型。公式、代码、术语和来源页必须人工核对。
- 本机评审入口不验证微信登录，不得开放到局域网或公网；一次性登录链接也应保密。
- 云托管、MySQL/COS 迁移、生产运行与微信真机需要独立验收，见[云部署说明](docs/wechat-cloud-hosting.md)。
- 私有评审包构建器仅供维护者使用，可能生成包含凭据的文件，不能作为公共发行包制作方法。

## 许可证与贡献

项目源码和文档采用 [MIT 许可证](LICENSE)。依赖保留各自条款，见[第三方声明](THIRD_PARTY_NOTICES.md)与[合成图来源](docs/ui-assets.md)。该许可证不授权使用未随源码分发的课堂照片、用户上传资料、外部服务或平台商标。

贡献流程见 [CONTRIBUTING.md](CONTRIBUTING.md)，安全问题报告见 [SECURITY.md](SECURITY.md)。本目录用于准备独立源码仓库，公开发布与打版本标签仍需单独执行。
