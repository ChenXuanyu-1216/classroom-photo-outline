"""Nonblocking local process locks that the operating system releases on exit."""
from contextlib import contextmanager
import errno
import os
from pathlib import Path


class LockBusyError(Exception):
    """Another thread or process owns this file's lock."""


@contextmanager
def exclusive_file_lock(path: Path):
    """Hold a stable file's first byte (Windows) or flock (POSIX).

    The file is intentionally persistent: unlinking an advisory lock can let
    different processes lock different inodes for the same resource. The OS,
    rather than the file's existence or a PID timestamp, owns the live claim.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise LockBusyError() from exc
                raise
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise LockBusyError() from exc
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
