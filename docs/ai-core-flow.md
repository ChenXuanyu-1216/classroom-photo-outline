# AI 核心流程：输入、上下文与结构化结果

本轮补强现有 DeepSeek 网页调用，不新增网页接口。流程是：读取已上传原图 → 生成适合模型处理的副本 → 组织本次请求上下文 → 调用模型 → 严格解析和校验 → 保存知识点与大纲。原图不被覆盖；坏图和超长上下文在付费请求前停止。

## 1. 输入预处理

`backend/app/services/image_preprocessor.py` 处理真实识别的 JPG/JPEG/PNG：

- 按大小上限读取文件，校验实际格式与扩展名一致，完整解码检查，拒绝损坏文件、多帧 PNG 和超限像素图片。
- 依据 EXIF 方向旋转或镜像；16 位灰度 PNG 在原始 16 位空间生成透明度掩码，再映射到 8 位并清除旧透明键，避免黑字丢失和相邻灰度误透明。
- 按原比例缩小长边，默认最多 2560 像素，不裁剪、不放大小图。透明区域合成白底，转换为 RGB，删除副本中的 EXIF、文本注释等元数据。
- PNG 优先保持无损编码；若编码副本超过字节限制则改为高质量 JPEG，仍超限时拒绝。JPEG 使用质量 92 编码，因此副本存在重新压缩；原文件保持不变。
- 在页级 `_meta.input` 中保存原图哈希、处理前后尺寸和字节数、旋转/缩放标记及 MIME，不保存密钥、原图内容或本地路径。

不做自动裁黑边、透视矫正、锐化或补字。纠正 EXIF 方向不能修复所有拍摄倾斜；缩放可能影响小字，真实课堂质量仍需人工评测。

实现依据：[Pillow 图像读取、验证和缩放](https://pillow.readthedocs.io/en/stable/reference/Image.html)、[EXIF 方向处理](https://pillow.readthedocs.io/en/stable/reference/ImageOps.html#PIL.ImageOps.exif_transpose)。依赖 `Pillow>=12.3.0,<13`，兼容项目 Python 3.10 环境。

## 2. 上下文组织

`backend/app/services/model_context.py` 每次重新构造上下文，不缓存上次任务正文：

- 图片识别：系统提示词与本张图片分开；用户 JSON 提供逻辑页码和 `scope=current_image_only`，不附桌面路径或其他任务资料。
- 主题归组：提供本任务知识点的临时 ID、原文、知识点类型、来源类型、来源页和对应页标题；`scope=current_task_only`。没有被当前知识点引用的页面标题不发送。
- 系统提示词及所有文字上下文按 UTF-8 字节计数，默认上限 96000；图片 base64 不计入此文字限制，受独立的图片限制和费用预留控制。超长返回 `MODEL_CONTEXT_TOO_LARGE`，不截断正文、不在本轮自动增加分块模型调用。
- 来源页格式或旧文字编码异常返回 `MODEL_CONTEXT_INVALID`。模型只分配知识点 ID，正文与全部来源仍由程序恢复。

提示词版本：识别 `1.3.0`，归组 `2.1.0`。课件文字始终视为待处理数据，不能修改系统指令。

## 3. 输出解析与结构化处理

`backend/app/services/model_response_parser.py` 统一解析识别、归组和拓展响应；`model_output.py` 只定义业务数据模型与结构约束，`app/errors.py` 负责将解析类别映射为公开错误契约：

- 只接受单个、正常结束的 completion；拒绝截断、内容过滤、工具调用和明确拒答。
- 接受纯 JSON 对象或完整的单一 JSON 代码围栏；不从解释文字中猜取片段，不自动修复半截 JSON。
- 拒绝重复 JSON 字段、NaN/Infinity、数字溢出、超大响应、非法类型和额外业务字段；限制标题、知识点和不确定项长度。保留正文中的大小写、空格、缩进、换行和公式符号。
- 识别结果含标题、知识点、来源类型、可读性及不确定项。不可读/没有知识点的页面识别失败；部分不可读或有不确定项时保守降低可读性置信度。这不是校准后的正确率。
- 归组结果只含模块名与知识点 ID；每个输入 ID 必须恰好出现一次，未知、重复和遗漏均拒绝。
- 空 HTTP 响应为 `MODEL_EMPTY_RESPONSE`，正常 completion 的空正文为 `MODEL_EMPTY_CONTENT`；其他格式异常沿用 `RECOGNITION_FAILED` / `CLUSTERING_FAILED` / `KNOWLEDGE_EXPANSION_FAILED`，识别页级格式原因为 `MODEL_OUTPUT_INVALID`。不暴露服务商正文。归组通过校验后才替换大纲，失败保留旧稿。

当前响应大小检查发生在 HTTP 客户端读取响应后，用于限制解析规模，不是流式下载内存上限。

## 配置、测试与使用

已有 `.env` 无须改动，新增配置有默认值；可按需要设置：

```dotenv
APP_IMAGE_MAX_EDGE=2560
APP_IMAGE_MAX_PIXELS=24000000
APP_MODEL_CONTEXT_MAX_BYTES=96000
```

先在 `backend` 目录运行 `.venv/Scripts/python.exe -m pip install -r requirements.txt`，再重启后端加载代码与新提示词。上传接收阶段仍按扩展名与字节数校验，实际图片内容在真实识别前校验；模拟模式仍可使用已有演示数据。

网页和脚本继续共用原有 5 元保守额度，本轮没有重置账本或新增付费测试，没有自动上传此前课堂照片。完整离线检查使用合成图片与模拟模型传输，覆盖预处理后的上传、识别、归组、保存、来源合并、坏页部分失败、旧稿保护及 Word 导出。验证记录见本轮记录（历史资料未随源码分发）。

## English

The existing model flow now decodes and validates JPG/JPEG/PNG, applies EXIF orientation, scales oversized images without cropping or enlarging, normalizes transparency and 16-bit grayscale, and strips metadata from the transmitted copy while retaining the upload.

Request-local context includes logical pages and source semantics. Text size is bounded in UTF-8 bytes; oversized input fails before spending, without silent truncation or extra model calls. A shared parser accepts a plain JSON object or one complete JSON fence, rejects duplicate fields, non-finite numbers, incomplete/refused completions and invalid schemas, and preserves code, formulas and original sources. Invalid grouping keeps the saved outline.

Install the updated requirements and restart the backend. The CNY 5 conservative allowance is unchanged. This delivery is verified with synthetic images and mocked HTTP, not new paid cloud tests or a classroom-quality benchmark.
