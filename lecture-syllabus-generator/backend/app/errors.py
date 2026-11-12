from fastapi import Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False, status_code: int = 400):
        self.code = code
        self.message = message
        self.retryable = retryable
        self.status_code = status_code
        super().__init__(message)


class TaskNotFoundError(AppError):
    def __init__(self):
        super().__init__(
            code="TASK_NOT_FOUND",
            message="任务不存在。",
            retryable=False,
            status_code=404,
        )


class FileFormatError(AppError):
    def __init__(self, detail: str = "文件格式不支持。"):
        super().__init__(
            code="FILE_FORMAT_ERROR",
            message=detail,
            retryable=False,
            status_code=400,
        )


class FileTooLargeError(AppError):
    def __init__(self):
        super().__init__(
            code="FILE_TOO_LARGE",
            message="文件大小超过限制。",
            retryable=False,
            status_code=400,
        )


class TooManyFilesError(AppError):
    def __init__(self):
        super().__init__(
            code="TOO_MANY_FILES",
            message="上传文件数量超过限制。",
            retryable=False,
            status_code=400,
        )


class ModelTimeoutError(AppError):
    def __init__(self):
        super().__init__(
            code="MODEL_TIMEOUT",
            message="模型调用超时，请重试。",
            retryable=True,
            status_code=504,
        )


class RecognitionFailedError(AppError):
    def __init__(self, detail: str = "识别失败。", retryable: bool = True, reason_code: str = 'RECOGNITION_FAILED'):
        self.reason_code = reason_code
        super().__init__(
            code="RECOGNITION_FAILED",
            message=detail,
            retryable=retryable,
            status_code=502,
        )


class PipelineError(AppError):
    def __init__(self, detail: str = "管道处理失败。"):
        super().__init__(
            code="PIPELINE_ERROR",
            message=detail,
            retryable=True,
            status_code=500,
        )


class ClusteringFailedError(AppError):
    def __init__(self, detail: str = "模型聚类失败，请重试或切换规则模式。", retryable: bool = True):
        super().__init__(
            code="CLUSTERING_FAILED",
            message=detail,
            retryable=retryable,
            status_code=502,
        )


def model_response_error(kind: str, stage: str) -> AppError:
    """Map a neutral parse category to the public business error contract."""
    label = {'recognition': '识别', 'grouping': '归组', 'expansion': '知识拓展'}[stage]
    if kind == 'empty_response':
        return AppError('MODEL_EMPTY_RESPONSE', f'{label}服务返回空响应，请稍后手动重试。', retryable=True, status_code=502)
    if kind == 'empty_content':
        return AppError('MODEL_EMPTY_CONTENT', f'{label}模型返回空正文，请稍后手动重试。', retryable=True, status_code=502)
    if kind != 'invalid_format':
        raise ValueError('Unknown model response failure kind')
    if stage == 'recognition':
        return RecognitionFailedError('模型返回格式无效或内容不完整，请重新识别。', reason_code='MODEL_OUTPUT_INVALID')
    if stage == 'grouping':
        return ClusteringFailedError('模型聚类返回格式无效或内容不完整，请重试。')
    return AppError('KNOWLEDGE_EXPANSION_FAILED', 'AI 知识拓展结果无效或不完整，请重试；已有大纲已保留。',
                    retryable=True, status_code=502)


async def app_error_handler(request: Request, exc: AppError):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "code": exc.code,
            "message": exc.message,
            "retryable": exc.retryable,
        },
    )


async def generic_error_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={
            "code": "INTERNAL_ERROR",
            "message": "服务器内部错误。",
            "retryable": False,
        },
    )
