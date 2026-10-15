"""One command at a time may change a project.

`run`, `sync` and writing the cookbook hold an operating-system lock on
work/.useful-views.lock while they work, so two of them started together
(an agent's parallel tool calls, a notebook and a terminal) cannot overwrite
each other's records or delete each other's views. The system releases the
lock when the process ends, however it ends, so it never needs cleaning up.
"""

from __future__ import annotations

import contextlib
import errno
import os
import threading
from pathlib import Path

from .errors import ProjectError
from .paths import WORK

LOCK_FILE = ".useful-views.lock"

# Re-entrant within a thread: sync() holds the lock and calls run().
_local = threading.local()

# On Windows the lock is a byte range, which no other process can read while
# it is held: lock one far past the process number written at the start.
WINDOWS_LOCK_OFFSET = 1 << 20

# Errors that mean another process holds the lock (msvcrt raises EACCES or
# EDEADLOCK; flock raises EWOULDBLOCK/EAGAIN, as BlockingIOError).
_HELD = {errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK, getattr(errno, "EDEADLOCK", errno.EDEADLK)}


def _try_lock(fd: int) -> bool:
    """Take the lock without waiting. False if another process holds it;
    True if taken, or if this file system cannot lock at all."""
    try:
        if os.name == "nt":
            import msvcrt

            os.lseek(fd, WINDOWS_LOCK_OFFSET, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno in _HELD:
            return False
        # Some network file systems cannot lock (ENOLCK, EOPNOTSUPP). Locking
        # is a safety net, so carry on without it rather than refuse.
    return True


def _unlock(fd: int) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            os.lseek(fd, WINDOWS_LOCK_OFFSET, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass  # closing the file releases it anyway


def _holder(path: Path) -> str:
    try:
        pid = path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError):
        return ""
    return f" (process {pid})" if pid.isdigit() else ""


@contextlib.contextmanager
def project_lock(project: Path, *, enabled: bool = True):
    """Hold the project's lock for the duration of the block.

    Raises:
        ProjectError: Another process holds it, or the lock file cannot be
            created (the project folder is not writable).
    """
    if not enabled:
        yield
        return
    held: set[Path] = _local.__dict__.setdefault("held", set())
    key = project.resolve()
    if key in held:  # this thread already holds it
        yield
        return

    path = project / WORK / LOCK_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o666)
    except PermissionError as exc:
        # Made by someone else on a shared project: a read-only handle still
        # locks (flock); only the process number cannot be written.
        try:
            fd = os.open(path, os.O_RDONLY)
        except OSError:
            raise ProjectError(
                f"Could not open {WORK}/{LOCK_FILE}: {exc.strerror or exc}. Check that the project folder is "
                "writable, or that you may write to that file (another user may have made it)."
            ) from exc
    except OSError as exc:
        raise ProjectError(
            f"Could not create {WORK}/{LOCK_FILE}: {exc.strerror or exc}. Check that the project folder is "
            "writable."
        ) from exc
    try:
        if not _try_lock(fd):
            raise ProjectError(
                f"Another useful-views command is already changing this project{_holder(path)}. Commands that "
                "change a project (run, sync, cookbook) run one at a time: wait for it to finish, then try again."
            )
        try:
            os.ftruncate(fd, 0)
            os.lseek(fd, 0, os.SEEK_SET)
            os.write(fd, f"{os.getpid()}\n".encode("ascii"))
        except OSError:
            pass  # the process number is only for the message above
        held.add(key)
        try:
            yield
        finally:
            held.discard(key)
            _unlock(fd)
    finally:
        os.close(fd)
