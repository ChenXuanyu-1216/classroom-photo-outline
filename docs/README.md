# 文档导航 / Documentation

当前源码包含网页、小程序、云模式、本机评审与评价证据工作区。不包含私有课程报告工具、真实课堂照片或历史 Git 仓库。

| 文档 | 用途 |
| --- | --- |
| [本地快速开始](local-quickstart.md) | 安装依赖、自己的密钥、仅回环评审入口及微信入口 |
| [开发参考](development.md) / [English](development.en.md) | 模型参数、上传契约、测试和数据库说明 |
| [小程序导入与调试](../miniprogram/README.md) | 空 AppID、云环境配置、局域网与平台要求 |
| [微信登录与归属](wechat-auth.md) | 网页扫码、服务端校验与任务隔离 |
| [云部署和显式迁移](wechat-cloud-hosting.md) | MySQL、私有 COS、后台执行与恢复 |
| [AI 核心流程](ai-core-flow.md) | 图片处理、上下文、结构化输出与失败处理 |
| [知识拓展](knowledge-expansion.md) | 原文与 AI 生成内容的区分 |
| [异常与恢复](v2-failure-handling.md) | 错误码、重试及记录边界 |
| [效果评价与证据](exp05-web-evaluation.md) | 终态快照、追加人工评价及导出 |
| [测试输入](exp05-test-inputs.md) | 合成样例、授权资料与故障注入 |
| [采集与空白模板](../evaluation/classroom-photos/README.md) | 不随源码分发真实照片；填写自己的授权与标注 |
| [人工评分协议](classroom-photo-rubric.md) | 忠实度、覆盖率、来源、重复组与不确定性 |
| [质量与可靠性方法](quality-reliability-acceptance.md) | 自备数据集、可选真实调用与统计口径 |
| [合成图来源](ui-assets.md) | UI 图片、测试图和许可边界 |
| [源码整合说明](source-reconciliation.md) | cloud-0002、证据导入与整合决策 |
| [源码发布状态](open-source-release.md) | 当前范围、验证记录和发布前事项 |
| [贡献](../CONTRIBUTING.md) / [安全](../SECURITY.md) | 开发约定与不公开披露敏感信息 |
| [许可证](../LICENSE) / [第三方声明](../THIRD_PARTY_NOTICES.md) | MIT、依赖与素材边界 |

历史验证只说明其记录日期的源码和环境，不代替当前源文件的复测。旧私有仓库的 PR、Actions、课程报告和照片包不作为本仓库的安装依赖。
