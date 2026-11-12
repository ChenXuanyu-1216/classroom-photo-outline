import io
from concurrent.futures import ThreadPoolExecutor
from itertools import permutations
from pathlib import Path

import pytest
from app.models import AppUser

pytestmark = pytest.mark.usefixtures("test_user")
from fastapi import UploadFile
from sqlalchemy.exc import OperationalError
from sqlalchemy import event

from app.config import settings
from app.db import SessionLocal, engine
from app.errors import AppError
from app.models import Page, Task
from app.routers import tasks


@pytest.fixture
def upload_root(tmp_path, monkeypatch):
    root = tmp_path / "uploads"
    root.mkdir()
    monkeypatch.setattr(tasks, "STORAGE_DIR", root)
    monkeypatch.setattr(settings, "max_file_mb", 1)
    return root


def upload(name="page.png", data=b"nonempty test content", stream=None):
    return UploadFile(filename=name, file=stream if stream is not None else io.BytesIO(data))


def assert_no_task_or_file(root):
    with SessionLocal() as db:
        assert db.query(Task).count() == 0
        assert db.query(Page).count() == 0
    assert list(root.iterdir()) == []


def persisted_pages(task_id):
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        pages = db.query(Page).filter_by(task_id=task_id).order_by(Page.page_no).all()
        assert task.page_count == len(pages)
        assert [p.page_no for p in pages] == list(range(1, len(pages) + 1))
        return [(p.filename, Path(p.file_path).read_bytes()) for p in pages]


@pytest.mark.parametrize("count", [1, 2])
def test_all_oversized_files_return_error_without_empty_task(client, upload_root, count):
    response = client.post("/api/tasks", files=[
        ("files", (f"large-{i}.png", b"x" * (1024 * 1024 + 1), "image/png"))
        for i in range(count)
    ])
    assert response.status_code == 400
    assert response.json()["code"] == "FILE_TOO_LARGE"
    assert_no_task_or_file(upload_root)


def test_mixed_extensions_keep_every_valid_file_without_explicit_order(upload_root):
    with SessionLocal() as db:
        response = tasks.create_task(files=[upload("bad.txt"), upload("a.png", b"a"), upload("b.pdf", b"b")], order="", db=db, user=AppUser(id=1))
    assert response.page_count == 2
    assert persisted_pages(response.task_id) == [("a.png", b"a"), ("b.pdf", b"b")]
    assert [item["filename"] for item in response.rejected] == ["bad.txt"]


def test_partial_rejections_keep_exact_bytes_and_contiguous_logical_pages(upload_root):
    with SessionLocal() as db:
        response = tasks.create_task(files=[
            upload("large.png", b"x" * (1024 * 1024 + 1)),
            upload("a.png", b"a"), upload("bad.txt"), upload("b.pdf", b"b"),
        ], order="4,3,1,2", db=db, user=AppUser(id=1))
    assert response.page_count == 2
    assert persisted_pages(response.task_id) == [("b.pdf", b"b"), ("a.png", b"a")]
    assert {item["filename"] for item in response.rejected} == {"large.png", "bad.txt"}
    assert sum(p.is_file() for p in upload_root.rglob("*")) == 2


@pytest.mark.parametrize("order", [
    "1,1", "1", "1,2,2", "0,2", "1,3", "-1,2", "1,x", "1,,2",
    ",1,2", "1,2,", "1.0,2", "1,٢", "9" * 5000,
], ids=["duplicate", "missing", "extra", "zero", "out-of-range", "negative", "text", "empty-token", "leading-comma", "trailing-comma", "decimal", "unicode-digit", "overlong"])
def test_invalid_order_is_a_client_error_and_has_no_side_effects(client, upload_root, order):
    response = client.post("/api/tasks", data={"order": order}, files=[
        ("files", ("a.png", b"a", "image/png")),
        ("files", ("b.png", b"b", "image/png")),
    ])
    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_FILE_ORDER"
    assert response.json()["retryable"] is False
    assert_no_task_or_file(upload_root)


@pytest.mark.parametrize("content", [b"", b"x" * (1024 * 1024 + 1)], ids=["empty", "oversized"])
def test_rejected_first_file_does_not_inflate_page_count(upload_root, content):
    with SessionLocal() as db:
        response = tasks.create_task(files=[upload("rejected.png", content), upload("good.png", b"good")], order="1,2", db=db, user=AppUser(id=1))
    assert response.page_count == 1
    assert persisted_pages(response.task_id) == [("good.png", b"good")]
    assert [item["filename"] for item in response.rejected] == ["rejected.png"]


def test_file_at_exact_size_limit_is_accepted(upload_root):
    content = b"a" * (1024 * 1024)
    with SessionLocal() as db:
        response = tasks.create_task(files=[upload(data=content)], order="", db=db, user=AppUser(id=1))
    assert persisted_pages(response.task_id) == [("page.png", content)]


def test_empty_batch_is_rejected_without_creating_a_task(upload_root):
    with SessionLocal() as db, pytest.raises(AppError):
        tasks.create_task(files=[], order="", db=db, user=AppUser(id=1))
    assert_no_task_or_file(upload_root)


def test_empty_file_is_rejected_without_creating_a_task(upload_root):
    with SessionLocal() as db, pytest.raises(AppError):
        tasks.create_task(files=[upload(data=b"")], order="", db=db, user=AppUser(id=1))
    assert_no_task_or_file(upload_root)


def test_upload_reads_are_bounded_and_save_the_complete_stream(upload_root):
    class BoundedReads(io.BytesIO):
        def read(self, size=-1):
            assert 0 < size <= 64 * 1024, "Upload reads must be bounded to 64 KiB"
            return super().read(size)

    content = b"a" * (170 * 1024 + 3)
    with SessionLocal() as db:
        response = tasks.create_task(files=[upload(stream=BoundedReads(content))], order="", db=db, user=AppUser(id=1))
    assert persisted_pages(response.task_id) == [("page.png", content)]


def test_read_failure_rolls_back_and_removes_partial_files(upload_root):
    class BrokenStream(io.BytesIO):
        def read(self, size=-1):
            if self.tell():
                raise OSError("simulated upload read failure")
            return super().read(min(size, 8) if size > 0 else 8)

    with SessionLocal() as db, pytest.raises(AppError) as error:
        tasks.create_task(files=[upload("first.png"), upload("broken.png", stream=BrokenStream(b"broken content"))], order="", db=db, user=AppUser(id=1))
    assert error.value.code == "UPLOAD_PERSISTENCE_ERROR"
    assert_no_task_or_file(upload_root)


def test_directory_publish_failure_rolls_back_database_and_staging(upload_root, monkeypatch):
    import os

    def fail_publish(source, destination, *args, **kwargs):
        raise OSError("simulated rename failure")

    monkeypatch.setattr(os, "rename", fail_publish)
    with SessionLocal() as db, pytest.raises(AppError) as error:
        tasks.create_task(files=[upload()], order="", db=db, user=AppUser(id=1))
    assert error.value.code == "UPLOAD_PERSISTENCE_ERROR"
    assert_no_task_or_file(upload_root)


def test_commit_failure_rolls_back_database_and_published_files(upload_root, monkeypatch):
    def fail_commit():
        raise OperationalError("COMMIT", {}, OSError("simulated database failure"))

    with SessionLocal() as db:
        monkeypatch.setattr(db, "commit", fail_commit)
        with pytest.raises(AppError) as error:
            tasks.create_task(files=[upload()], order="", db=db, user=AppUser(id=1))
        assert error.value.code == "UPLOAD_PERSISTENCE_ERROR"
    assert_no_task_or_file(upload_root)


def test_generated_permutations_and_rejections_match_an_independent_model(upload_root):
    cases = 0
    for count in range(1, 5):
        masks = range(1 << count) if count < 4 else [1, 5, 10, 15]
        for order in permutations(range(count)):
            for mask in masks:
                names = [f"{i}.png" if mask & (1 << i) else f"{i}.txt" for i in range(count)]
                expected = [(names[i], str(i).encode()) for i in order if mask & (1 << i)]
                files = [upload(name, str(i).encode()) for i, name in enumerate(names)]
                with SessionLocal() as db:
                    if not expected:
                        with pytest.raises(AppError):
                            tasks.create_task(files=files, order=",".join(str(i + 1) for i in order), db=db, user=AppUser(id=1))
                    else:
                        result = tasks.create_task(files=files, order=",".join(str(i + 1) for i in order), db=db, user=AppUser(id=1))
                        assert result.page_count == len(expected)
                        assert len(result.rejected) == count - len(expected)
                        assert persisted_pages(result.task_id) == expected
                cases += 1
    assert cases == 154


def test_declared_size_cannot_bypass_bounded_actual_size_validation(upload_root):
    stream = io.BytesIO(b"x" * (2 * 1024 * 1024))
    file = UploadFile(filename="large.png", file=stream, size=1)
    with SessionLocal() as db, pytest.raises(AppError) as error:
        tasks.create_task(files=[file], order="", db=db, user=AppUser(id=1))
    assert error.value.code == "FILE_TOO_LARGE"
    assert stream.tell() == 1024 * 1024 + 1
    assert_no_task_or_file(upload_root)


def test_partial_disk_write_failure_removes_partial_file_and_rows(upload_root, monkeypatch):
    original_open = Path.open

    class PartialWrite:
        def __init__(self, output):
            self.output = output

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.output.close()

        def write(self, content):
            self.output.write(content[:3])
            raise OSError("simulated disk full after partial write")

    def failing_open(path, mode="r", *args, **kwargs):
        result = original_open(path, mode, *args, **kwargs)
        if mode == "xb" and path.parent.parent == upload_root:
            return PartialWrite(result)
        return result

    monkeypatch.setattr(Path, "open", failing_open)
    with SessionLocal() as db, pytest.raises(AppError) as error:
        tasks.create_task(files=[upload()], order="", db=db, user=AppUser(id=1))
    assert error.value.code == "UPLOAD_PERSISTENCE_ERROR"
    assert_no_task_or_file(upload_root)


@pytest.mark.parametrize("operation", ["mkdir", "fsync", "page-flush"])
def test_other_persistence_failures_leave_no_rows_or_files(upload_root, monkeypatch, operation):
    import os
    import tempfile

    def fail(*args, **kwargs):
        raise OSError("simulated persistence failure")

    def fail_page(*args, **kwargs):
        raise OperationalError("INSERT", {}, OSError("simulated page insert failure"))

    if operation == "mkdir":
        monkeypatch.setattr(tempfile, "mkdtemp", fail)
    elif operation == "fsync":
        monkeypatch.setattr(os, "fsync", fail)
    else:
        event.listen(Page, "before_insert", fail_page)
    try:
        with SessionLocal() as db, pytest.raises(AppError) as error:
            tasks.create_task(files=[upload()], order="", db=db, user=AppUser(id=1))
        assert error.value.code == "UPLOAD_PERSISTENCE_ERROR"
        assert_no_task_or_file(upload_root)
    finally:
        if operation == "page-flush":
            event.remove(Page, "before_insert", fail_page)


@pytest.mark.parametrize("failure", ["lost-ack", "after-commit-event"])
def test_error_after_durable_commit_does_not_delete_committed_files(upload_root, monkeypatch, failure):
    def after_commit(db):
        raise OSError("simulated error after database commit")

    with SessionLocal() as db:
        if failure == "lost-ack":
            commit = db.commit

            def lose_acknowledgement():
                commit()
                raise OperationalError("COMMIT", {}, OSError("simulated lost commit acknowledgement"))

            monkeypatch.setattr(db, "commit", lose_acknowledgement)
        else:
            event.listen(db, "after_commit", after_commit)
        result = tasks.create_task(files=[upload("saved.png", b"must survive")], order="", db=db, user=AppUser(id=1))
    assert persisted_pages(result.task_id) == [("saved.png", b"must survive")]


def test_failed_request_preserves_existing_tasks_and_unrelated_files(upload_root, monkeypatch):
    with SessionLocal() as db:
        original = tasks.create_task(files=[upload("original.png", b"keep")], order="", db=db, user=AppUser(id=1))
    legacy = upload_root / "1"
    legacy.mkdir()
    sentinel = legacy / "keep.bin"
    sentinel.write_bytes(b"legacy user data")
    original_directories = set(upload_root.iterdir())

    def fail_commit():
        raise OperationalError("COMMIT", {}, OSError("simulated failure"))

    with SessionLocal() as db:
        monkeypatch.setattr(db, "commit", fail_commit)
        with pytest.raises(AppError):
            tasks.create_task(files=[upload("new.png")], order="", db=db, user=AppUser(id=1))
    assert persisted_pages(original.task_id) == [("original.png", b"keep")]
    assert sentinel.read_bytes() == b"legacy user data"
    assert set(upload_root.iterdir()) == original_directories
    with SessionLocal() as db:
        assert db.query(Task).count() == 1


def test_concurrent_uploads_preserve_each_batch_without_directory_collisions(upload_root):
    def create(index):
        with SessionLocal() as db:
            return tasks.create_task(files=[upload(f"{index}.png", str(index).encode())], order="", db=db, user=AppUser(id=1))

    with ThreadPoolExecutor(max_workers=4) as workers:
        results = list(workers.map(create, range(8)))
    assert len({result.task_id for result in results}) == 8
    for index, result in enumerate(results):
        assert persisted_pages(result.task_id) == [(f"{index}.png", str(index).encode())]
    assert len(list(upload_root.iterdir())) == 8
    assert not list(upload_root.glob(".upload-*"))


@pytest.mark.parametrize("committed", [False, True], ids=["rolled-back", "committed"])
def test_unverifiable_commit_preserves_files_and_prevents_blind_retry(upload_root, monkeypatch, committed):
    verification_unavailable = False

    def fail_reads(connection, cursor, statement, parameters, context, executemany):
        if verification_unavailable and statement.lstrip().upper().startswith("SELECT"):
            raise OperationalError(statement, parameters, OSError("simulated database unavailable during verification"))

    event.listen(engine, "before_cursor_execute", fail_reads)
    try:
        with SessionLocal() as db:
            original_commit = db.commit

            def lose_commit_result():
                nonlocal verification_unavailable
                if committed:
                    original_commit()
                verification_unavailable = True
                raise OperationalError("COMMIT", {}, OSError("simulated unknown outcome"))

            monkeypatch.setattr(db, "commit", lose_commit_result)
            with pytest.raises(AppError) as error:
                tasks.create_task(files=[upload(data=b"retain until verified")], order="", db=db, user=AppUser(id=1))
            assert error.value.code == "UPLOAD_STATE_UNCERTAIN"
            assert error.value.retryable is False
    finally:
        event.remove(engine, "before_cursor_execute", fail_reads)
    files = [path for path in upload_root.rglob("*") if path.is_file()]
    assert len(files) == 1
    assert files[0].read_bytes() == b"retain until verified"
    with SessionLocal() as db:
        assert db.query(Task).count() == int(committed)


def test_cleanup_failure_is_explicit_and_does_not_claim_success(upload_root, monkeypatch, caplog):
    import shutil

    def fail_cleanup(path, *args, **kwargs):
        raise PermissionError("simulated locked file")

    monkeypatch.setattr(shutil, "rmtree", fail_cleanup)
    with SessionLocal() as db, pytest.raises(AppError) as error:
        tasks.create_task(files=[upload(data=b"")], order="", db=db, user=AppUser(id=1))
    assert error.value.code == "UPLOAD_CLEANUP_ERROR"
    assert error.value.retryable is False
    with SessionLocal() as db:
        assert db.query(Task).count() == 0
        assert db.query(Page).count() == 0
    assert "manual storage reconciliation required" in caplog.text


def test_task_is_not_visible_while_file_bytes_are_still_being_read(upload_root):
    class VisibilityProbe(io.BytesIO):
        def read(self, size=-1):
            with SessionLocal() as observer:
                assert observer.query(Task).count() == 0
                assert observer.query(Page).count() == 0
            return super().read(size)

    with SessionLocal() as db:
        result = tasks.create_task(files=[upload(stream=VisibilityProbe(b"still being uploaded"))], order="", db=db, user=AppUser(id=1))
    assert persisted_pages(result.task_id) == [("page.png", b"still being uploaded")]


def test_one_failed_concurrent_request_cannot_remove_successful_batches(upload_root):
    def create(index):
        with SessionLocal() as db:
            if index == 0:
                def fail_commit():
                    raise OperationalError("COMMIT", {}, OSError("simulated one failed writer"))

                db.commit = fail_commit
                with pytest.raises(AppError) as error:
                    tasks.create_task(files=[upload("failed.png")], order="", db=db, user=AppUser(id=1))
                assert error.value.code == "UPLOAD_PERSISTENCE_ERROR"
                return None
            return tasks.create_task(files=[upload(f"{index}.png", str(index).encode())], order="", db=db, user=AppUser(id=1))

    with ThreadPoolExecutor(max_workers=4) as workers:
        results = list(workers.map(create, range(4)))
    for index, result in enumerate(results[1:], 1):
        assert persisted_pages(result.task_id) == [(f"{index}.png", str(index).encode())]
    assert len(list(upload_root.iterdir())) == 3
    with SessionLocal() as db:
        assert db.query(Task).count() == 3
        assert db.query(Page).count() == 3
