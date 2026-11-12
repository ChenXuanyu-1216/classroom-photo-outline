# AI 知识拓展

真实识别模式现在会根据本任务的课堂知识点，额外调用一次文本模型生成相关分支、先修概念、应用示例或常见误区。拓展结果独立列在“AI 知识拓展”章节，每条带 `【AI生成】`；原有课堂正文与来源类型保留。

例如，课件只有机器学习定义时，可以补充监督学习、无监督学习和强化学习等分支及解释。具体结果由模型按输入主题生成，代码没有写死机器学习答案。提示词期望每任务 3–6 条，结构上接受 1–8 条；重复原文的条目被移除，如果没有新内容则报错。

## 启用和使用

已有 DeepSeek 接入可直接使用，新增配置默认开启：

```dotenv
APP_MODEL_PROVIDER=deepseek
APP_CLUSTER_PROVIDER=model
APP_KNOWLEDGE_EXPANSION_ENABLED=true
```

兼容 Chat Completions JSON 输出的服务也可以使用 `APP_MODEL_PROVIDER=multimodal`。聚类仍可独立选择 `rule` 或 `model`。`mock`、`fallback` 不调用拓展模型；设 `APP_KNOWLEDGE_EXPANSION_ENABLED=false` 可关闭拓展。

修改代码或配置后重启后端。新上传任务在生成大纲时自动拓展；已有任务需要在大纲页选择“重新生成”，此操作会覆盖当前大纲和人工编辑。旧稿不会在打开时自动改写或产生模型费用。

## 标注与保存

```text
## 一、课堂主题
- 图片中的定义（来源：P1）【课堂原话】

## 二、AI 知识拓展
以下为 AI 生成的拓展知识，供学习参考，请自行核对；关联课堂页仅表示主题依据，不表示这些内容出现在原课件中。
- 与定义有关的延伸知识及解释（关联课堂页：P1）【AI生成】
```

“关联课堂页”表示拓展基于哪页的课堂主题，不表示补充文字来自原图，也不是外部资料的引文。模型只能引用本次请求的临时知识点 ID；程序校验 ID 并计算关联页，统一填写 `AI生成`，不接受模型自报 `课堂原话` 或页码。

拓展知识点写入 `knowledge_point` 和 `outline_node`，同时随 Markdown 保存。网页显示、编辑保存后重新打开、Word 导出均保留标签与关联页。旧 `AI推断` 标签仍用于课堂题目的推演等已有内容，与新的课外拓展标签分别展示。来源类型字段已有足够长度，无须新增数据库列。

大纲工具栏提供“← 返回整理记录”，直接跳转到 `/history.html`，方便从知识点页面回到任务列表。

重新生成从已保存的图片识别结果开始，不把上次的拓展当成课堂原文送回模型。从模拟/未核验记录切换到真实识别时，会重新读取图片；失败页不参与拓展依据。

## 失败与费用

拓展使用现有密钥、超时、有限重试、上下文字节上限和 DeepSeek 保守预算账本；每个实际尝试均先预留额度。每次真实大纲生成会比原来多一次文本请求，重试可能增加请求数，保守预留不是实际扣款。

缺密钥、错误地址或提示词返回 `EXPANSION_CONFIG_ERROR`；无效或截断输出、无效引用、只有重复内容及服务故障返回 `KNOWLEDGE_EXPANSION_FAILED`。超时、上下文和预算错误沿用现有错误码。只有暂时的网络/服务故障才有限重试；输出校验失败不自动重试。

拓展通过校验后才替换已保存的知识点和大纲。失败时任务显示失败、保留旧稿和已保存的识别结果，可在恢复服务后重试。它不会静默跳过拓展并宣称生成成功。

这是模型生成的学习参考，没有联网检索或外部文献核验；结构校验和 AI 标注不能保证每句话学术正确。跨学科准确性、课程深度与相关程度仍需人工评估。

## 重复测试

离线测试不发送付费请求，默认隔离数据库和上传目录。新增拓展测试会单独开启该功能，并只替换外部 HTTP 传输：

```powershell
cd lecture-syllabus-generator/backend
.\.venv\Scripts\python.exe -m pytest -q
```

真实闭环测试脚本默认只检查配置：

```powershell
# 从仓库根目录执行；不调用模型
lecture-syllabus-generator\backend\.venv\Scripts\python.exe lecture-syllabus-generator/scripts/check_knowledge_expansion.py

# 明确发起真实调用
lecture-syllabus-generator\backend\.venv\Scripts\python.exe lecture-syllabus-generator/scripts/check_knowledge_expansion.py --live
```

真实测试自动绘制一张只有英文机器学习定义的合成 PPT，经上传、真实识别、归组、拓展、编辑保存、重新读取和 Word 导出，检查原文与监督/无监督/强化学习是否分别存在、标注是否保留。使用独立的测试数据库和存储目录，共享原有预算账本，不重置额度、不读取已有私人课堂照片；本测试关闭网络重试。

结果、合成输入、大纲及 Word 位于被 Git 忽略的 `backend/data/knowledge-expansion-check/<本轮目录>/`。本次实测与截图见验证记录（历史资料未随源码分发）。
