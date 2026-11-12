"""Bound JSON upload bytes before decoding or Pydantic validation."""
from starlette.responses import JSONResponse


class UploadBodyLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or not scope.get('path', '').startswith('/api/files/uploads') or scope.get('method') not in {'POST', 'PUT'}:
            return await self.app(scope, receive, send)
        parts, size = [], 0
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            value = message.get('body', b'')
            size += len(value)
            if size > 512 * 1024:
                response = JSONResponse(status_code=413, content={
                    'code': 'REQUEST_TOO_LARGE', 'message': '上传请求过大，请使用分块上传。', 'retryable': False})
                return await response(scope, receive, send)
            parts.append(value)
            if not message.get('more_body', False):
                break
        used = False
        async def replay():
            nonlocal used
            if used:
                return await receive()
            used = True
            return {'type': 'http.request', 'body': b''.join(parts), 'more_body': False}
        return await self.app(scope, replay, send)
