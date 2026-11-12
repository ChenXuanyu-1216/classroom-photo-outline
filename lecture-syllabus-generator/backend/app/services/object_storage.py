"""Private source storage; container files are bounded disposable caches only."""
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path, PurePosixPath
import json
import math
import os
import re
import tempfile
import time
from urllib.parse import urlsplit

from app.config import settings, STORAGE_DIR
from app.errors import AppError
from app.services.wechat_storage_credentials import CloudRunCredentialProvider

CHUNK_SIZE = 256 * 1024
MAX_READ = 32 * 1024 * 1024
_cloudrun_credentials = CloudRunCredentialProvider()


def _invalid():
    return AppError('STORAGE_REFERENCE_INVALID', '文件引用无效。', status_code=404)


def _failure(error):
    if isinstance(error, AppError):
        return error
    code = getattr(error, 'get_status_code', lambda: None)()
    if isinstance(error, FileNotFoundError) or str(code) == '404':
        return AppError('SOURCE_NOT_FOUND', '原图或文件不存在。', status_code=404)
    return AppError('CLOUD_STORAGE_UNAVAILABLE', '文件存储暂不可用，请稍后重试。', retryable=True, status_code=503)


def _key(key):
    if not isinstance(key, str) or not key.startswith('kezhao/') or len(key) > 400:
        raise _invalid()
    if not re.fullmatch(r'[A-Za-z0-9/_\-.]+', key) or any(part in ('', '.', '..') for part in key.split('/')):
        raise _invalid()
    return key


class Storage:
    def read(self, reference, *, limit=MAX_READ):
        size = self.size(reference)
        if not 0 <= size <= limit <= MAX_READ:
            raise AppError('FILE_TOO_LARGE', '文件超过允许的读取大小。', status_code=413)
        parts = []
        for offset in range(0, size, CHUNK_SIZE):
            expected = min(CHUNK_SIZE, size - offset)
            value = self.read_range(reference, offset, expected)
            if len(value) != expected:
                raise AppError('CLOUD_STORAGE_INCOMPLETE', '文件读取不完整，请重试。', retryable=True, status_code=503)
            parts.append(value)
        return b''.join(parts)

    @contextmanager
    def materialize(self, reference, *, limit=MAX_READ):
        content = self.read(reference, limit=limit)
        suffix = PurePosixPath(urlsplit(reference).path).suffix
        with tempfile.TemporaryDirectory(prefix='kezhao-source-') as temporary:
            path = Path(temporary) / ('source' + suffix)
            path.write_bytes(content)
            yield path


class LocalStorage(Storage):
    def __init__(self, root):
        self.root = Path(root).resolve()

    def _path(self, reference):
        path = Path(reference).resolve()
        if path == self.root or not path.is_relative_to(self.root):
            raise _invalid()
        return path

    def reference(self, key):
        return str(self._path(self.root / _key(key)))

    def put(self, key, content):
        path = self._path(self.reference(key))
        data = content.read_bytes() if isinstance(content, Path) else bytes(content)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                if path.read_bytes() != data:
                    raise AppError('STORAGE_CONFLICT', '文件写入冲突。', status_code=409)
            else:
                with path.open('xb') as output:
                    output.write(data)
            return str(path)
        except Exception as error:
            raise _failure(error) from None

    def size(self, reference):
        try:
            return self._path(reference).stat().st_size
        except Exception as error:
            raise _failure(error) from None

    def read_range(self, reference, offset, length):
        if type(offset) is not int or offset < 0 or type(length) is not int or not 0 <= length <= CHUNK_SIZE:
            raise _invalid()
        try:
            with self._path(reference).open('rb') as source:
                source.seek(offset)
                return source.read(length)
        except Exception as error:
            raise _failure(error) from None

    def delete(self, reference):
        try:
            self._path(reference).unlink(missing_ok=True)
        except Exception as error:
            raise _failure(error) from None

    @contextmanager
    def materialize(self, reference, *, limit=MAX_READ):
        if self.size(reference) > limit:
            raise AppError('FILE_TOO_LARGE', '文件超过允许的读取大小。', status_code=413)
        yield self._path(reference)


def _credentials():
    if settings.cos_credentials_source == 'wechat-cloudrun':
        if settings.deployment_mode != 'cloud':
            raise AppError('COS_CREDENTIALS_UNAVAILABLE', '文件存储凭据来源与运行模式不符。', status_code=503)
        return _cloudrun_credentials.get()
    if settings.cos_credentials_file:
        try:
            document = json.loads(Path(settings.cos_credentials_file).read_text(encoding='utf-8'))
            expiry = float(document['expires_at'])
            values = tuple(document[field] for field in ('secret_id', 'secret_key', 'token'))
            if not math.isfinite(expiry) or expiry <= time.time() + 30 or not all(
                isinstance(value, str) and value for value in values
            ):
                raise ValueError()
            return values
        except Exception:
            raise AppError('COS_CREDENTIALS_UNAVAILABLE', '文件存储临时凭据需要更新。', status_code=503) from None
    result = (settings.cos_secret_id or os.environ.get('TENCENTCLOUD_SECRETID', ''),
              settings.cos_secret_key or os.environ.get('TENCENTCLOUD_SECRETKEY', ''),
              settings.cos_session_token or os.environ.get('TENCENTCLOUD_SESSIONTOKEN', ''))
    if not result[0] or not result[1]:
        raise AppError('COS_CREDENTIALS_UNAVAILABLE', '文件存储凭据尚未配置。', status_code=503)
    return result


@lru_cache(maxsize=2)
def _client(region, secret_id, secret_key, token):
    from qcloud_cos import CosConfig, CosS3Client
    return CosS3Client(CosConfig(Region=region, SecretId=secret_id, SecretKey=secret_key,
                                Token=token or None, Scheme='https', Timeout=30))


class CosStorage(Storage):
    def __init__(self, bucket, region, *, client=None):
        self.bucket, self.region, self._provided_client = bucket, region, client

    @property
    def client(self):
        return self._provided_client if self._provided_client is not None else _client(self.region, *_credentials())

    def reference(self, key):
        return f'cos://{self.bucket}/{_key(key)}'

    def _parse(self, reference):
        value = urlsplit(reference)
        if value.scheme != 'cos' or value.netloc != self.bucket or value.query or value.fragment:
            raise _invalid()
        return _key(value.path.removeprefix('/'))

    def put(self, key, content):
        key = _key(key)
        try:
            if isinstance(content, Path):
                with content.open('rb') as source:
                    self.client.put_object(Bucket=self.bucket, Key=key, Body=source, ACL='private')
            else:
                self.client.put_object(Bucket=self.bucket, Key=key, Body=bytes(content), ACL='private')
            return self.reference(key)
        except Exception as error:
            raise _failure(error) from None

    def size(self, reference):
        key = self._parse(reference)
        try:
            value = int(self.client.head_object(Bucket=self.bucket, Key=key)['Content-Length'])
            if value < 0:
                raise ValueError()
            return value
        except Exception as error:
            raise _failure(error) from None

    def read_range(self, reference, offset, length):
        key = self._parse(reference)
        if type(offset) is not int or offset < 0 or type(length) is not int or not 0 <= length <= CHUNK_SIZE:
            raise _invalid()
        if length == 0:
            return b''
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key, Range=f'bytes={offset}-{offset + length - 1}')
            source = response['Body'].get_raw_stream()
            try:
                content = source.read(length + 1)
            finally:
                source.close()
            if len(content) > length:
                raise ValueError('Storage ignored range limit')
            return content
        except Exception as error:
            raise _failure(error) from None

    def delete(self, reference):
        key = self._parse(reference)
        try:
            self.client.delete_object(Bucket=self.bucket, Key=key)
        except Exception as error:
            if str(getattr(error, 'get_status_code', lambda: None)()) != '404':
                raise _failure(error) from None


def get_storage():
    if settings.storage_backend == 'cos':
        return CosStorage(settings.cos_bucket, settings.cos_region)
    return LocalStorage(STORAGE_DIR)
