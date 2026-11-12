# 测试输入与证据记录

本页用于源码开发者的测试输入准备，不代表学校课程要求，也不分发真实课件、冻结金标准或历史费用账本。

## 可用输入

- 合成图片 `lecture-syllabus-generator/backend/tests/fixtures/deepseek-synthetic-slide.png`，用于公式、代码、保存和导出的流程检查。
- 后端测试中的临时图片、数据库与模拟 HTTP 响应，用于 timeout、connection、empty body/content、malformed JSON 和 empty points 等故障。
- [输入清单模板](../evaluation/classroom-photos/templates/exp05-input-manifest.template.json)与[问题记录模板](../evaluation/classroom-photos/templates/v2-issue-record.template.json)，需要自行填写；它们不是自动执行器。
- 自备的新课件及明确授权。记录来源、顺序、预处理、哈希与实际允许用途，参见[采集说明](../evaluation/classroom-photos/README.md)。

## 最小人工流程

1. 使用你有权处理的输入；真实模型调用另行确认云上传权限与费用。
2. 上传多页并检查顺序；识别后核对代码、公式、术语及来源。
3. 编辑保存、返回历史、重开、查看原图并导出 Word。
4. 验证失败后原稿保留、明确重试和断网后的状态恢复。
5. 在[评价工作区](exp05-web-evaluation.md)保存终态快照及人工评价，记录实际版本、日期、模型、mock/live 状态、付费与执行结果。

个人数据、授权记录及新运行证据放在被忽略的 `evaluation/classroom-photos/local/`。公共模板保留空值。历史测试数、金额或截图不能替代当前运行证据；参见[发布状态](open-source-release.md)。
