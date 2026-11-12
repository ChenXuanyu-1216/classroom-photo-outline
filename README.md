<h1 align="center">课堂 PPT 照片智能大纲生成器</h1>

<p align="center">
  <b>把课件照片整理成能编辑、能回看来源的复习大纲。</b><br>
  按页上传，校对内容，保存后导出 Word。
</p>

<p align="center">
  <a href="LICENSE.zh-CN.md"><img src="https://img.shields.io/badge/license-MIT-6b5b4b?style=flat-square" alt="MIT 许可证"></a>
  <img src="https://img.shields.io/badge/Python-3.10%2B-6b5b4b?style=flat-square" alt="Python 3.10 及以上">
  <a href="https://github.com/ChenXuanyu-1216/classroom-photo-outline/actions/workflows/checks.yml"><img src="https://github.com/ChenXuanyu-1216/classroom-photo-outline/actions/workflows/checks.yml/badge.svg?branch=main" alt="工程检查"></a>
</p>

<p align="center"><b>简体中文</b> · <a href="README.en.md">English</a></p>
<p align="center">
  <a href="#这是什么">项目介绍</a> ·
  <a href="#在本机跑起来">本机启动</a> ·
  <a href="#自己改哪些地方">配置与修改</a> ·
  <a href="#文档">文档</a> ·
  <a href="https://github.com/ChenXuanyu-1216/classroom-photo-outline/releases/tag/v0.1.0">下载 v0.1.0</a>
</p>

## 这是什么

上传一组按顺序排好的课件照片，程序会识别页面内容，把相关知识点整理到一起，生成带来源页码的大纲。你可以对着原图校对，改好后保存，回到整理记录继续查看，也可以导出 Word。

源码里包含 FastAPI 后端、浏览器界面和原生微信小程序。图片处理、识别提示词、归组、知识拓展和导出都能在代码里找到，开发者可以沿着实际流程修改。

[v0.1.0](https://github.com/ChenXuanyu-1216/classroom-photo-outline/releases/tag/v0.1.0) 已作为开发者预览版发布，提供源码安装。公式、代码、术语和来源仍需要人工核对。目前没有公共托管服务，也没有 Windows 安装包。

## 你会用到什么

| 事情 | 已有的功能 |
| --- | --- |
| 整理一组照片 | 调整页序，识别页面，跨页归组，保留来源页码 |
| 继续改大纲 | 编辑并保存，回到历史记录重新打开，查看原图，导出 Word |
| 区分课堂内容与补充解释 | 显示原文来源与 AI 生成标记，便于逐项校对 |
| 记录一次评价 | 保存终态快照，追加人工评价，导出 JSON 或 Markdown 证据 |
| 在电脑上先试用 | 用自己的 DeepSeek 密钥启动本机评审入口，无需配置微信 |
| 接入微信或云端 | 网页扫码、小程序登录和任务归属检查，以及显式配置的 MySQL、私有 COS 与后台执行 |

程序收到模型结果后，会检查格式和业务字段。遇到超时、空结果或内容格式异常，会返回对应错误。mock 模式返回预设示例，真实照片识别需要自己的模型配置。

## 先看一眼

下载源码后，用浏览器打开 [frontend-preview.html](frontend-preview.html)，可以先看静态界面。这个文件不连接后端，也不处理你上传的照片。

需要实际走一遍上传、编辑和导出时，继续按下面的方法启动。项目带有一张[合成课件示例](lecture-syllabus-generator/backend/tests/fixtures/deepseek-synthetic-slide.png)，可以复用。真实课堂照片、私有标注和他人的使用凭据不随源码分发。

## 在本机跑起来

准备 Git、Python 3.10 或更新版本，以及你自己的 DeepSeek API 密钥。网页不需要 npm 安装或前端构建。下面先启动只供本机使用的评审入口。

在 Windows PowerShell 中执行。

```powershell
git clone https://github.com/ChenXuanyu-1216/classroom-photo-outline.git
cd classroom-photo-outline/lecture-syllabus-generator/backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
New-Item -ItemType Directory -Force ../../.reviewer-data | Out-Null
if (-not (Test-Path ../../.reviewer-data/.env.reviewer)) {
    Copy-Item .env.reviewer.example ../../.reviewer-data/.env.reviewer
}
```

打开本机的 `.reviewer-data/.env.reviewer`，在 `APP_MODEL_API_KEY` 中填入自己的密钥，再从后端目录启动。

```powershell
.\.venv\Scripts\python.exe reviewer.py --data-dir ../../.reviewer-data
```

<details>
<summary>macOS 和 Linux 对应命令</summary>

克隆后进入 `lecture-syllabus-generator/backend`，执行下面的命令。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
mkdir -p ../../.reviewer-data
test -e ../../.reviewer-data/.env.reviewer || cp .env.reviewer.example ../../.reviewer-data/.env.reviewer
# 在私有配置文件中填入自己的 APP_MODEL_API_KEY 后再启动。
.venv/bin/python reviewer.py --data-dir ../../.reviewer-data
```

</details>

启动后，终端会给出一次性登录链接，并尝试打开浏览器。链接在五分钟内可使用一次。服务只监听 `127.0.0.1`，按 Ctrl+C 停止。再次启动时使用同一个数据目录，才能继续查看已保存的内容。

上传合成示例后，可以检查识别结果，修改并保存一段文字，再到历史记录重开、导出 Word。真实识别、归组和知识拓展可能产生 API 费用。示例配置启用了应用内的本地预算检查，它不能替代服务商的账单与计费控制。

本机评审入口固定使用官方 DeepSeek 配置，没有 mock 回退。常规网页和微信小程序的登录方式不同，mock 模型模式也保留登录要求。配置方法见[完整快速开始](docs/local-quickstart.md)和[微信登录说明](docs/wechat-auth.md)。

## 自己改哪些地方

| 位置 | 适合修改的内容 |
| --- | --- |
| `backend/.env.example`、`.env.reviewer.example`、`.env.cloud.example` | 本地、评审与云模式的配置示例，填写后的副本留在本机 |
| `lecture-syllabus-generator/backend/prompts/` | 识别、归组和知识拓展的提示词 |
| `lecture-syllabus-generator/backend/app/` | 接口、任务、模型调用、存储和导出 |
| `lecture-syllabus-generator/frontend/` | 网页界面与交互 |
| `miniprogram/` | 微信小程序页面、传输方式和客户端配置 |
| `evaluation/classroom-photos/templates/` | 自己的数据清单、授权和人工评价记录 |

表中三个后端配置示例位于 `lecture-syllabus-generator/backend/`。小程序的 AppID、云环境 ID 和服务名称默认留空，需要换成自己的配置。手机访问电脑后端时，要使用电脑可达的地址，手机上的 `127.0.0.1` 指向手机本身。

本地开发使用 SQLite 和文件存储。云模式还需要 MySQL、私有 COS、独立后台执行和显式数据库迁移，部署前请读[云端说明](docs/wechat-cloud-hosting.md)。

## 测试

后端目录中运行以下命令。

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
```

网页和小程序测试需要 Node.js 24。在项目根目录运行。

```powershell
node --test lecture-syllabus-generator/frontend/tests/*.test.cjs
node --test miniprogram/tests/*.test.cjs
.\lecture-syllabus-generator\backend\.venv\Scripts\python.exe -m pytest scripts/tests/test_reviewer_runtime.py -q
```

macOS 和 Linux 使用相应的 `.venv/bin/python` 路径。完整后端验收中的版本取证需要 Git 和已有提交。仅下载 ZIP、尚未建立 Git 历史时，可以先在后端运行 `.\.venv\Scripts\python.exe -m pytest -q tests/test_smoke.py`，macOS 和 Linux 使用 `.venv/bin/python`。

v0.1.0 对应的[工程检查](https://github.com/ChenXuanyu-1216/classroom-photo-outline/actions/runs/43195153409)已通过，包含离线回归、MySQL 8.0 与 8.4，以及容器构建和启动检查。离线测试使用临时数据和模拟上游，不能据此推定真实课堂识别准确率、微信真机体验或生产部署已经验收。

## 文档

| 文档 | 内容 |
| --- | --- |
| [文档导航](docs/README.md) | 安装、模型、存储、评价与验证记录 |
| [本地快速开始](docs/local-quickstart.md) | 自己的密钥、本机评审和常规微信入口 |
| [小程序说明](miniprogram/README.md) | 导入、手机网络、云配置和平台要求 |
| [AI 处理流程](docs/ai-core-flow.md) | 图片处理、上下文和结构化结果检查 |
| [评价工作区](docs/exp05-web-evaluation.md) | 证据快照、人工评价和导出 |
| [评测模板](evaluation/classroom-photos/README.md) | 自备数据、用途授权和评分方法 |
| [发布状态](RELEASE_STATUS.md) | 已发布内容与尚待验证的范围 |

## 交流与贡献

遇到可复现的问题，可以[提交 Issue](https://github.com/ChenXuanyu-1216/classroom-photo-outline/issues/new)，说明使用入口、操作步骤、预期结果和脱敏后的错误信息。准备改代码时，先看[贡献说明](CONTRIBUTING.md)。

小程序发布、真实模型费用和部署环境需要由运行者自行配置。提交反馈时，不要附上密钥、会话或未经许可的课堂照片。

## 安全与隐私

模型密钥和微信 AppSecret 只保存在后端。`.env`、`.reviewer-data`、数据库、上传原图和私有包都应留在受控的本机或服务端目录。

`reviewer.py` 及其一次性登录链接只供本机使用，不要用公网代理暴露。发现漏洞时，请按[安全说明](SECURITY.md)寻找私密报告方式，避免在公开 Issue 中贴出漏洞细节或敏感资料。

## MIT 许可证

项目源码和文档采用 [MIT 许可证](LICENSE)。你可以使用、修改和分发，也可以用于商业用途，但需要保留版权与许可声明。完整中文译文见 [LICENSE.zh-CN.md](LICENSE.zh-CN.md)，正式许可文本保留在英文 `LICENSE` 中。

依赖有各自的许可，见[第三方声明](THIRD_PARTY_NOTICES.md)。真实课堂照片、用户上传资料、外部服务和平台商标不因代码开源而获得额外授权。[合成示例图](docs/ui-assets.md)的来源单独列明。
