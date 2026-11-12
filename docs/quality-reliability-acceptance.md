# 质量与可靠性检查方法

本页提供可选的测量方法和已有脚本入口，不声明当前版本达到某个课堂准确率、长期成功率或公开服务可用性门槛。源码不附带真实课堂照片或冻结金标准。离线回归与模型质量应分别判断。

## 数据与人工评价

使用[空白模板](../evaluation/classroom-photos/README.md)记录自己的数据、用途授权、页序和哈希。正式评价前独立标注、解决分歧并冻结期望；AI 辅助草稿不能当作独立人工金标准。按[评分协议](classroom-photo-rubric.md)分别记录忠实度、覆盖率、来源正确率、模块组织、公式和代码错误及不可读区域。

跨页重复应是不同页面中的自然重复。重拍、裁切或模糊同一张照片不能补足独立样本。失败、空结果和弃答应按事先确定的协议保留，不能只挑较好的重试输出。

## 内容检查脚本

从 `lecture-syllabus-generator` 目录运行。旧默认试标包不随源码提供，必须显式传入自己的数据集：

```powershell
# 只校验/准备清单，不调用模型。
.\backend\.venv\Scripts\python.exe scripts/run_quality_acceptance.py --dataset ../evaluation/classroom-photos/local/YOUR_DATASET/dataset.json
```

macOS/Linux 将 Python 路径换成 `backend/.venv/bin/python`。清单和图片必须满足脚本实际校验；一个空模板不等于完整输入。

只有在确认输入的外部模型上传授权和费用后，才增加 `--live --authorization-record PATH`。授权应对应数据包及图片 SHA-256，不能继承历史数据的许可。`--reuse-recognition-from RUN --refresh-samples ID1 ID2` 仅复用匹配的原图识别并刷新指定样本；复用结果不能记为新的识别成功样本。

## 可靠性检查

```powershell
# 默认演练，不读取真实密钥、不发起模型调用。
.\backend\.venv\Scripts\python.exe scripts/run_reliability_acceptance.py probe
# 读取本地 evidence 并汇总，不进行模型调用。
.\backend\.venv\Scripts\python.exe scripts/run_reliability_acceptance.py summarize --output backend/data/reliability-summary.json
```

`probe --live` 是一次显式授权的真实探测，不是定时器。运行前核对自己的模型配置、金额限制和原有记录。失败、超时及结果不确定均应保留；未开始的调用不算成功。不要通过删除费用账本或重开预算绕过限制。应用估算不替代服务商账单或计费控制。

报告应包含样本和分母、执行日期、版本、模型、提示词版本、mock/live 状态、失败分类、授权、费用以及人工复核方式。少量成功不能证明长期高可用。若使用 Wilson 比例区间，应预先定义抽样与时间窗口，保留全部执行结果；方法参见 [NIST](https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm)。

运行输出放在被忽略的 `backend/data/acceptance`、`backend/data/reliability` 或 `evaluation/classroom-photos/local/`。公共报告仅保留脱敏结论。当前工程验证见[发布状态](open-source-release.md)，不要从这份方法说明推定实测已通过。


## 命令行的云上传授权 JSON

`--authorization-record` 接收 JSON 文件，不能传入人类填写的 Markdown 授权表。复制[机器可读空模板](../evaluation/classroom-photos/templates/cloud-authorization.template.json)到私有目录，仅在已有真实授权证据后填写 `provider` 为 `deepseek`、匹配的数据集 `dataset_id`、`cloud_upload_allowed` 为 `true`，并在 `sample_sha256` 中逐项映射样本 ID 到原图 SHA-256。模板默认 `false`，不代表获得许可。Markdown 表用于保存用途和证据引用，JSON 用于脚本的逐样本授权校验。

完整内容/可靠性验收的版本取证需要 Git 和至少一个提交。尚未初始化 Git 的源码文件夹可以使用本机评审与 smoke 测试；完整验收应在有 HEAD 的源码检出中执行。
