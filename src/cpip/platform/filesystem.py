from __future__ import annotations

import os
import os.path
from collections.abc import Generator
from contextlib import contextmanager
from functools import wraps
from tempfile import NamedTemporaryFile
from time import perf_counter, sleep
from typing import Any, BinaryIO, Callable, ParamSpec, TypeVar, cast


P = ParamSpec("P")
R = TypeVar("R")


def retry(
    *,
    wait: float,
    stop_after_delay: float,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    def decorate(func: Callable[P, R]) -> Callable[P, R]:
        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            deadline = perf_counter() + stop_after_delay
            while True:
                try:
                    return func(*args, **kwargs)
                except BaseException as exc:
                    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                        raise
                    if perf_counter() > deadline:
                        raise
                    if wait:
                        sleep(wait)

        return cast("Callable[P, R]", wrapper)

    return decorate


def format_size(size: float) -> str:
    if size > 1000 * 1000:
        return f"{size / (1000 * 1000):.1f} MB"
    if size > 1000:
        return f"{size / 1000:.1f} kB"
    return f"{size:.0f} bytes"


@contextmanager
def adjacent_tmp_file(
    path: str,
    *,
    durable: bool = True,
    **kwargs: Any,
) -> Generator[BinaryIO, None, None]:
    """Return a file-like object pointing to a tmp file next to path.

    The file is created securely. With ``durable`` (the default) it is
    fsynced to disk after the context reaches its end; pass
    ``durable=False`` for data that may be regenerated, such as cache
    entries, where an entry lost to a crash only costs a refetch and the
    fsync would dominate the write.

    kwargs will be passed to tempfile.NamedTemporaryFile to control
    the way the temporary file will be opened.
    """
    with NamedTemporaryFile(
        delete=False,
        dir=os.path.dirname(path),
        prefix=os.path.basename(path),
        suffix=".tmp",
        **kwargs,
    ) as f:
        result = cast("BinaryIO", f)
        try:
            yield result
        finally:
            result.flush()
            if durable:
                os.fsync(result.fileno())


replace = retry(stop_after_delay=1, wait=0.25)(os.replace)


def set_file_permissions(target_file: BinaryIO, mode: int) -> None:
    if os.chmod in os.supports_fd:
        os.chmod(target_file.fileno(), mode)
    elif os.chmod in os.supports_follow_symlinks:
        os.chmod(target_file.name, mode, follow_symlinks=False)


def copy_directory_permissions(directory: str, target_file: BinaryIO) -> None:
    set_file_permissions(target_file, os.stat(directory).st_mode & 0o666 | 0o600)
