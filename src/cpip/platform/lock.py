"""Advisory file lock serializing writers of one installation target.

Two installers driving the same environment concurrently can interleave
file writes -- pip maintainers demonstrated a runnable race with two wheels
shipping the same file (pypa/pip#8187) -- so the install transaction takes an
exclusive lock on a file beside the installed packages, the way uv locks the
target environment.  Advisory only: it orders cooperating cpip processes and
asks nothing of other tools.
"""

from __future__ import annotations

import contextlib
import errno
import logging
import os
import sys
from collections.abc import Iterator

logger = logging.getLogger(__name__)

LOCK_FILE_NAME = ".cpip-install.lock"


def _lock_exclusive(fd: int) -> None:
    """Block until ``fd`` holds the exclusive lock."""

    if sys.platform == "win32":
        import msvcrt

        # LK_LOCK retries for ~10 seconds and then raises; loop as long as
        # the error still means "someone else holds it" so acquisition
        # blocks like flock does.
        while True:
            try:
                msvcrt.locking(fd, msvcrt.LK_LOCK, 1)

            except OSError as exc:
                if exc.errno in {errno.EACCES, errno.EDEADLK}:
                    continue

                raise

            else:
                return

    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX)


def _unlock(fd: int) -> None:
    if sys.platform == "win32":
        import msvcrt

        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)


@contextlib.contextmanager
def environment_write_lock(directory: str) -> Iterator[None]:
    """Hold an exclusive advisory lock on ``directory`` for the block.

    When the lock file cannot be created or locked (read-only media, an
    exotic target, a filesystem without locks) the block runs unlocked,
    which is exactly the pre-lock behavior: the lock reduces risk where it
    can and never turns a working install into a failure.
    """

    path = os.path.join(directory, LOCK_FILE_NAME)

    try:
        os.makedirs(directory, exist_ok=True)

        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)

    except OSError:
        logger.debug("Could not create %s; installing without a lock", path)

        yield

        return

    try:
        try:
            _lock_exclusive(fd)

        except OSError:
            logger.debug("Could not lock %s; installing without a lock", path)

            yield

            return

        yield

    finally:
        with contextlib.suppress(OSError):
            _unlock(fd)

        os.close(fd)
