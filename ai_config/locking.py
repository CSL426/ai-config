"""Cross-platform process lock for apply and project mutations."""

import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import BinaryIO

from .paths import BACKUP_BASE
from .safety import assert_root_not_reparse, assert_safe_write_target


def _try_lock(handle: BinaryIO) -> bool:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def _unlock(handle: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def apply_lock(timeout: float = 10.0) -> Iterator[None]:
    assert_root_not_reparse(BACKUP_BASE, "backup root")
    BACKUP_BASE.mkdir(parents=True, exist_ok=True)
    assert_root_not_reparse(BACKUP_BASE, "backup root")
    lock_path = BACKUP_BASE / ".ai-config-backup.lock"
    assert_safe_write_target(lock_path)

    with lock_path.open("a+b") as handle:
        if os.name == "nt" and lock_path.stat().st_size == 0:
            handle.write(b"\0")
            handle.flush()
        deadline = time.monotonic() + timeout
        while not _try_lock(handle):
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Timed out waiting for apply lock: {lock_path}")
            time.sleep(0.05)
        try:
            yield
        finally:
            _unlock(handle)
