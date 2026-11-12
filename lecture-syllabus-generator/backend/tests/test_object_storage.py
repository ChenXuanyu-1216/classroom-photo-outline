from pathlib import Path

import pytest

from app.errors import AppError
from storage_fakes import FakeCosClient


def test_private_object_survives_materialized_cache_removal():
    from app.services.object_storage import CosStorage
    client = FakeCosClient()
    store = CosStorage('fixture-bucket', 'ap-guangzhou', client=client)
    reference = store.put('kezhao/source/fixture/page.png', b'original bytes')
    assert reference == 'cos://fixture-bucket/kezhao/source/fixture/page.png'
    assert client.access[('fixture-bucket', 'kezhao/source/fixture/page.png')] == 'private'
    with store.materialize(reference) as path:
        assert path.read_bytes() == b'original bytes'
        cached = path
    assert not cached.exists()
    assert store.read(reference, limit=100) == b'original bytes'
    assert store.read_range(reference, 9, 5) == b'bytes'


@pytest.mark.parametrize('reference', ['cos://another-bucket/kezhao/secret',
                                       'cos://fixture-bucket/kezhao/../secret',
                                       'https://untrusted.invalid/photo.png'])
def test_cos_rejects_forged_or_escaped_object_references(reference):
    from app.services.object_storage import CosStorage
    store = CosStorage('fixture-bucket', 'ap-guangzhou', client=FakeCosClient())
    with pytest.raises(AppError):
        store.read(reference, limit=100)


def test_large_object_cannot_escape_the_read_limit():
    from app.services.object_storage import CosStorage
    store = CosStorage('fixture-bucket', 'ap-guangzhou', client=FakeCosClient())
    reference = store.put('kezhao/source/fixture/large.png', b'x' * 100)
    with pytest.raises(AppError):
        store.read(reference, limit=99)
    with pytest.raises(AppError):
        store.read_range(reference, 0, 256 * 1024 + 1)
    store.delete(reference)
    store.delete(reference)
    with pytest.raises(AppError) as missing:
        store.size(reference)
    assert missing.value.status_code == 404


def test_local_adapter_never_reads_or_deletes_outside_its_root(tmp_path):
    from app.services.object_storage import LocalStorage
    root = tmp_path / 'owned'
    other = tmp_path / 'keep.txt'
    other.write_text('keep me')
    store = LocalStorage(root)
    reference = store.put('kezhao/source/page.png', b'local original')
    with store.materialize(reference) as path:
        assert path.read_bytes() == b'local original'
    assert Path(reference).is_file()
    for operation in [lambda: store.read(str(other), limit=100), lambda: store.delete(str(other)),
                      lambda: store.put('../keep.txt', b'overwrite')]:
        with pytest.raises(AppError):
            operation()
    assert other.read_text() == 'keep me'


def test_projected_credentials_rotate_without_restarting_the_service(monkeypatch, tmp_path):
    import json
    import time
    from app.config import settings
    from app.services.object_storage import _credentials
    path = tmp_path / 'temporary-credentials.json'
    monkeypatch.setattr(settings, 'cos_credentials_file', str(path))
    monkeypatch.setattr(settings, 'cos_secret_id', 'static-fallback-must-not-win')
    monkeypatch.setattr(settings, 'cos_secret_key', 'synthetic')
    def write(token):
        path.write_text(json.dumps({'secret_id': 'temporary-id', 'secret_key': 'temporary-key',
                                    'token': token, 'expires_at': time.time() + 600}))
    write('first-token')
    assert _credentials() == ('temporary-id', 'temporary-key', 'first-token')
    write('rotated-token')
    assert _credentials() == ('temporary-id', 'temporary-key', 'rotated-token')
    path.write_text(json.dumps({'secret_id': 'temporary-id', 'secret_key': 'temporary-key',
                               'token': 'expired-token', 'expires_at': 0}))
    with pytest.raises(AppError) as expired:
        _credentials()
    assert expired.value.code == 'COS_CREDENTIALS_UNAVAILABLE'
