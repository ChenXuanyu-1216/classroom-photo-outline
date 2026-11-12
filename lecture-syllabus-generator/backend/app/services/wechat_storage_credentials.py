"""COS credentials from WeChat Cloud Hosting's internal open-interface service.

This fixed HTTP endpoint is intercepted by the platform, not a public credential
service. Call only when the explicit cloud credential source has been selected.
"""
import json
import math
import threading
import time

import httpx

from app.errors import AppError

ENDPOINT = 'http://api.weixin.qq.com/_/cos/getauth'
MAX_RESPONSE_BYTES = 16 * 1024
REFRESH_MARGIN_SECONDS = 60


class CloudRunCredentialProvider:
    def __init__(self):
        self._lock = threading.Lock()
        self._cached = None
        self._refresh_wall = self._refresh_monotonic = 0

    def get(self) -> tuple[str, str, str]:
        with self._lock:
            if (self._cached is not None and time.time() < self._refresh_wall
                    and time.monotonic() < self._refresh_monotonic):
                return self._cached
            # Never fall back to stale credentials after a failed refresh.
            self._cached = None
            try:
                with httpx.Client(timeout=10, trust_env=False, follow_redirects=False) as client:
                    with client.stream('GET', ENDPOINT) as response:
                        response.raise_for_status()
                        body = bytearray()
                        for chunk in response.iter_bytes(chunk_size=4096):
                            if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                                raise ValueError()
                            body.extend(chunk)
                document = json.loads(body)
                if not isinstance(document, dict) or document.get('errcode', 0) != 0:
                    raise ValueError()
                values = tuple(document[field] for field in ('TmpSecretId', 'TmpSecretKey', 'Token'))
                if not all(isinstance(value, str) and value.strip() for value in values):
                    raise ValueError()
                raw_expiry = document['ExpiredTime']
                if isinstance(raw_expiry, bool):
                    raise ValueError()
                expiry = float(raw_expiry)
                now = time.time()
                if not math.isfinite(expiry) or expiry <= now + REFRESH_MARGIN_SECONDS:
                    raise ValueError()
                self._refresh_wall = expiry - REFRESH_MARGIN_SECONDS
                self._refresh_monotonic = time.monotonic() + self._refresh_wall - now
                self._cached = values
                return values
            except (httpx.HTTPError, ValueError, TypeError, KeyError, OverflowError, RecursionError):
                # Do not log response bodies, exceptions, or any credential fields.
                raise AppError('COS_CREDENTIALS_UNAVAILABLE',
                               '文件存储临时凭据暂不可用，请检查云调用授权后重试。',
                               retryable=True, status_code=503) from None
