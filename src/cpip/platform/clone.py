"""Lightweight copy-on-write cloning primitives.



Installation hot paths import this module without pulling in the broader

filesystem utility stack.  Platform-specific fallback modules are loaded only

when the native clone operation is unavailable.

"""

from __future__ import annotations

import errno
import os
import stat
import sys

TYPE_CHECKING = False

if TYPE_CHECKING:
    from collections.abc import Callable

    CloneFile = Callable[[bytes, bytes, int], int]

_FICLONE = 0x40049409

_clonefile: CloneFile | None = None

_clonefile_loaded = False


def _darwin_clone(source: str, destination: str) -> bool:
    """Clone one file or directory tree with APFS copy-on-write semantics."""

    global _clonefile, _clonefile_loaded

    if sys.platform != "darwin":
        return False

    import ctypes

    if not _clonefile_loaded:
        _clonefile_loaded = True

        try:
            function = ctypes.CDLL(None, use_errno=True).clonefile

        except (AttributeError, OSError):
            function = None

        if function is not None:
            function.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int)

            function.restype = ctypes.c_int

        _clonefile = function

    function = _clonefile

    if function is None:
        return False

    if (
        function(
            os.fsencode(source),
            os.fsencode(destination),
            0,
        )
        == 0
    ):
        return True

    error = ctypes.get_errno()

    if error in {
        errno.ENOTSUP,
        errno.EXDEV,
        errno.EINVAL,
        getattr(errno, "ENOSYS", -1),
    }:
        return False

    raise OSError(error, os.strerror(error), destination)


_reflink_unsupported: set[str] = set()

"""Destination directories whose filesystem rejected FICLONE outright.

``ioctl`` is issued on the destination descriptor, so support is a property
of the destination filesystem, and one that does not change mid-run.  Without
this, a cache tree cloned onto ext4, overlayfs or tmpfs pays an open, an
exclusive create, the failing ioctl and an unlink for every file before
falling back to a copy.  Keying on the directory rather than ``st_dev`` keeps
the check free: the caller already holds the path, whereas a device number
costs the stat this is trying to avoid.
"""


def _linux_reflink(source: str, destination: str) -> bool:
    """Clone one regular file with the Linux FICLONE ioctl when available."""

    if not sys.platform.startswith("linux"):
        return False

    destination_parent = os.path.dirname(destination)

    if destination_parent in _reflink_unsupported:
        return False

    try:
        import fcntl

    except ImportError:
        return False

    source_fd = os.open(source, os.O_RDONLY)

    try:
        mode = stat.S_IMODE(os.fstat(source_fd).st_mode)

        destination_fd = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            mode,
        )

        try:
            fcntl.ioctl(destination_fd, _FICLONE, source_fd)

        except OSError as exc:
            os.close(destination_fd)

            destination_fd = -1

            try:
                os.unlink(destination)

            except FileNotFoundError:
                pass

            if exc.errno in {
                errno.ENOTTY,
                errno.ENOSYS,
                errno.EOPNOTSUPP,
            }:
                _reflink_unsupported.add(destination_parent)

                return False

            if exc.errno in {errno.EXDEV, errno.EINVAL}:
                return False

            raise

        finally:
            if destination_fd >= 0:
                os.close(destination_fd)

    finally:
        os.close(source_fd)

    import shutil

    shutil.copystat(source, destination, follow_symlinks=False)

    return True


def clone_path(source: str, destination: str) -> None:
    """Copy a cache path using copy-on-write cloning whenever possible.



    Both paths must be absent from concurrent mutation. If ``destination`` is

    an existing directory, directory contents are merged while duplicate files

    are rejected.

    """

    source_text = os.fspath(source)

    destination_text = os.fspath(destination)

    destination_exists = os.path.lexists(destination_text)

    if not destination_exists:
        try:
            if _darwin_clone(source_text, destination_text):
                return

        except OSError as exc:
            if exc.errno != errno.EEXIST:
                raise

            destination_exists = True

    if not destination_exists:
        source_is_link = os.path.islink(source_text)

        return _clone_absent(
            source_text,
            destination_text,
            os.path.isdir(source_text) and not source_is_link,
            source_is_link,
        )

    if not (
        os.path.isdir(source_text)
        and not os.path.islink(source_text)
        and os.path.isdir(destination_text)
        and not os.path.islink(destination_text)
    ):
        raise FileExistsError(
            errno.EEXIST,
            "copy-on-write destination already exists",
            destination_text,
        )

    with os.scandir(source_text) as entries:
        for entry in entries:
            clone_path(
                os.path.join(source_text, entry.name),
                os.path.join(destination_text, entry.name),
            )


def _clone_absent(
    source: str,
    destination: str,
    is_directory: bool,
    is_symlink: bool,
) -> None:
    """Clone ``source`` onto a ``destination`` known not to exist.

    ``is_directory`` and ``is_symlink`` are passed in because the directory
    walk below already has them from ``scandir``, which answers both from the
    ``readdir`` result.  Re-deriving them per entry -- as recursing through
    ``clone_path`` did -- costs an ``lexists``, an ``isdir`` and an ``islink``
    on every file in the tree.
    """

    if is_directory:
        source_mode = stat.S_IMODE(os.stat(source).st_mode)

        try:
            os.mkdir(destination, source_mode | stat.S_IWUSR | stat.S_IXUSR)

        except FileExistsError:
            return clone_path(source, destination)

        import shutil

        try:
            with os.scandir(source) as entries:
                for entry in entries:
                    _clone_absent(
                        entry.path,
                        os.path.join(destination, entry.name),
                        entry.is_dir(follow_symlinks=False),
                        entry.is_symlink(),
                    )

            # Restores the source mode, including the owner write and search
            # bits added above.  A read-only source directory cannot be
            # created read-only up front: its own children could not then be
            # written into it.  Only owner bits are added, so the directory is
            # never briefly more permissive to anyone else.
            shutil.copystat(source, destination, follow_symlinks=False)

        except BaseException:
            shutil.rmtree(destination, ignore_errors=True)

            raise

        return

    if is_symlink:
        os.symlink(os.readlink(source), destination)

        return

    if not _linux_reflink(source, destination):
        import shutil

        shutil.copy2(source, destination, follow_symlinks=False)
