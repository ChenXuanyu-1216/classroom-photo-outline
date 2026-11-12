# Classroom-photo evaluation templates / 课堂照片评测模板

This source distribution contains blank templates and text-based regression checks. **It contains no real classroom photos, pilot dataset, frozen human gold standard or private permission records.** Use the synthetic slide under `lecture-syllabus-generator/backend/tests/fixtures/` for reusable offline examples.

源码仅提供空白模板与文本回归检查，不分发真实课堂照片、历史试标数据或人工金标准。模板本身不是应用接口，也不会自动运行评测。

## 模板与方法

| 文件 | 用途 |
| --- | --- |
| [授权记录](templates/authorization-record.template.md) | 来源、证据引用、允许用途与确认日期 |
| [数据集清单](templates/dataset.template.json) | 数据及图片哈希；以文件实际字段为准 |
| [页级期望](templates/page-expectation.template.json) | 可见知识单元、公式、代码及不确定性 |
| [输入清单](templates/exp05-input-manifest.template.json) | 本次执行输入与模式 |
| [问题记录](templates/v2-issue-record.template.json) | 复现步骤、错误与修复证据 |
| [人工评分协议](../../docs/classroom-photo-rubric.md) | 忠实度、覆盖率、来源正确率与模块复核 |
| [质量和可靠性方法](../../docs/quality-reliability-acceptance.md) | 显式传入数据集和授权，不默认使用排除的数据 |

## 数据和授权

将自己的资料放在 `local/<dataset-id>/`，该目录被 Git 忽略。建议保留匿名课程代号、课次、页序、场景、原图及预处理后哈希；普通中文、公式、代码、低清晰度、自然跨页重复与近似但含义不同的页面应分别记录。

允许本地评测、允许上传到指定模型服务、允许向 GitHub 公开和允许通用再分发是独立用途；未知记为 pending，不得相互推定。含姓名、学号、人脸等信息时，先取得适用许可并制作脱敏副本，记录处理与哈希。不要把真实内容填进公共模板后提交。

先独立标注可见原文，再处理分歧和冻结期望。AI 辅助草稿不是独立人工标注或金标准。低清晰度页面应保留不可读区域，不能借清晰对照补写原文。同页裁剪、重拍和降质衍生图不能冒充不同页面或自然跨页重复。

## 运行

内容回归使用源码中的 `content-regressions.json` 与合成测试图，不要求真实照片。质量清单工具的旧默认数据集不随源码提供，必须显式传入你自己的数据集：

```powershell
# 从 lecture-syllabus-generator 目录运行；仅准备/检查，不调用模型。
.ackend\.venv\Scripts\python.exe scripts/run_quality_acceptance.py --dataset ../evaluation/classroom-photos/local/YOUR_DATASET/dataset.json
```

确认数据和云上传授权后，才考虑显式增加 `--live --authorization-record PATH`。真实调用可能收费；未运行的模板不能记成已通过。数据集结构应以工具校验和实际模板字段为准，不能仅创建一个同名 JSON 文件代替完整输入。

项目代码许可证不替用户授予课堂资料或外部模型输出的再分发权限。


## 命令行的云上传授权 JSON

`--authorization-record` 接收 JSON 文件，不能传入人类填写的 Markdown 授权表。复制[机器可读空模板](templates/cloud-authorization.template.json)到私有目录，仅在已有真实授权证据后填写 `provider` 为 `deepseek`、匹配的数据集 `dataset_id`、`cloud_upload_allowed` 为 `true`，并在 `sample_sha256` 中逐项映射样本 ID 到原图 SHA-256。模板默认 `false`，不代表获得许可。Markdown 表用于保存用途和证据引用，JSON 用于脚本的逐样本授权校验。

完整内容/可靠性验收的版本取证需要 Git 和至少一个提交。尚未初始化 Git 的源码文件夹可以使用本机评审与 smoke 测试；完整验收应在有 HEAD 的源码检出中执行。
