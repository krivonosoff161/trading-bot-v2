"""Shared no-follow OS lock for synthetic storage mutations."""

from __future__ import annotations

import ctypes
import os
import stat
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from src.research_lab.storage_capability import is_link_or_reparse

msvcrt: Any
fcntl: Any
_WIN_DLL: Any = getattr(ctypes, "WinDLL", None)
_GET_LAST_ERROR: Any = getattr(ctypes, "get_last_error", lambda: 0)
try:  # pragma: no cover - imported on the matching platform
    import msvcrt as _msvcrt
except ImportError:  # pragma: no cover
    msvcrt = None
else:
    msvcrt = _msvcrt
try:  # pragma: no cover - imported on the matching platform
    import fcntl as _fcntl
except ImportError:  # pragma: no cover
    fcntl = None
else:
    fcntl = _fcntl


class StorageLockConflict(RuntimeError):
    """The shared storage mutation lock cannot be proved held."""


def _stat_identity(info: os.stat_result) -> dict[str, int]:
    return {
        "device": int(info.st_dev),
        "inode": int(info.st_ino),
        "size": int(info.st_size),
        "mtime_ns": int(info.st_mtime_ns),
    }


def _path_identity(path: Path) -> dict[str, int]:
    info = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode) or is_link_or_reparse(path):
        raise StorageLockConflict("storage operation lock path is unsafe")
    return _stat_identity(info)


def _open_lock_nofollow(path: Path) -> tuple[Any, dict[str, int]]:
    before = path.lstat()
    if is_link_or_reparse(path) or not stat.S_ISREG(before.st_mode):
        raise StorageLockConflict("storage operation lock path is unsafe")
    if os.name != "nt":
        flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
    else:
        if msvcrt is None or _WIN_DLL is None:  # pragma: no cover
            raise StorageLockConflict("Windows lock handle support is unavailable")
        kernel32 = _WIN_DLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.restype = ctypes.c_void_p
        raw_handle = create_file(
            ctypes.c_wchar_p(str(path)),
            0xC0000000,
            0x00000001 | 0x00000002,
            None,
            3,
            0x00200000,
            None,
        )
        if raw_handle in (None, ctypes.c_void_p(-1).value):
            raise OSError(_GET_LAST_ERROR(), "CreateFileW failed", str(path))
        try:
            fd = msvcrt.open_osfhandle(
                int(raw_handle), os.O_RDWR | getattr(os, "O_BINARY", 0)
            )
        except Exception:
            kernel32.CloseHandle(ctypes.c_void_p(raw_handle))
            raise
    handle = os.fdopen(fd, "r+b", closefd=True)
    opened = os.fstat(handle.fileno())
    if _stat_identity(opened) != _stat_identity(before) or is_link_or_reparse(path):
        handle.close()
        raise StorageLockConflict("storage operation lock identity changed")
    return handle, _stat_identity(opened)


_LOCAL_LOCK = threading.Lock()
_LOCAL_OWNER: int | None = None


@contextmanager
def storage_root_lock(path: Path, *, wait_seconds: float = 0.0) -> Iterator[None]:
    """Hold the one process and OS lock; reentry always fails."""
    global _LOCAL_OWNER
    owner = threading.get_ident()
    if _LOCAL_OWNER == owner:
        raise StorageLockConflict(
            "storage operation lock is already held by this thread"
        )
    acquired = _LOCAL_LOCK.acquire(timeout=max(0.0, float(wait_seconds)))
    if not acquired:
        raise StorageLockConflict("storage operation lock is already held")
    _LOCAL_OWNER = owner
    handle = None
    locked = False
    try:
        handle, lock_identity = _open_lock_nofollow(path)
        try:
            if os.name == "nt":
                if msvcrt is None:  # pragma: no cover
                    raise StorageLockConflict("Windows OS locking is unavailable")
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                if fcntl is None:  # pragma: no cover
                    raise StorageLockConflict("POSIX OS locking is unavailable")
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
            if _path_identity(path) != lock_identity:
                raise StorageLockConflict("storage operation lock identity changed")
        except (OSError, BlockingIOError) as exc:
            raise StorageLockConflict("cannot acquire storage operation lock") from exc
        yield
    finally:
        if handle is not None:
            try:
                if locked and os.name == "nt" and msvcrt is not None:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                elif locked and fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()
        _LOCAL_OWNER = None
        _LOCAL_LOCK.release()
