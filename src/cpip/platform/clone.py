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
import time

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


_reflink_slow: set[int] = set()

"""Devices whose FICLONE succeeds but moves data at copy speed.

A working reflink is metadata work and finishes in microseconds regardless of
file size; on some filesystems (network-backed block storage, XFS
configurations) the ioctl succeeds but behaves like a full copy, which made
uv's clone-by-default install ~30x slower than hardlinking on XFS-on-EBS
(astral-sh/uv#18259).  Successful clones of probe-sized files are timed while
a device is undecided, and a device whose measured throughput stays below
what any metadata-only clone achieves is demoted to the plain-copy fallback.
Keyed by ``st_dev`` from the ``fstat`` the mode read already pays for: a
successful FICLONE implies source and destination share that device.
"""

_reflink_fast: set[int] = set()

"""Devices whose measured clone throughput proved FICLONE is metadata work."""

_reflink_probe: dict[int, tuple[int, int]] = {}

"""Per-device ``(bytes, ns)`` accumulated over timed clones while undecided.

Installer threads race on these entries without a lock: a lost update only
lengthens the probe, and ``_reflink_slow`` membership is the load-bearing
outcome.
"""

# Files below this size prove nothing: the fixed ioctl cost dominates, and a
# genuinely instant clone of tiny files would read as slow throughput.
_REFLINK_PROBE_MIN_FILE_BYTES = 128 * 1024

# Judge a device once this much time went into its timed ioctls...
_REFLINK_PROBE_MIN_NS = 25_000_000

# ...demoting it when the bytes cloned in that time imply less than ~256
# MB/s: no metadata-only clone is that slow, and a clone that behaves as a
# full copy of cache-fresh files rarely exceeds it.
_REFLINK_MIN_BYTES_PER_NS = 0.256

# A device cloning this much before reaching the time threshold implies
# multi-GB/s throughput, which no data copy explains; it is proven fast.
_REFLINK_PROVE_FAST_BYTES = 64 * 1024 * 1024


def _record_reflink_timing(device: int, size: int, elapsed_ns: int) -> None:
    """Fold one timed clone into the device's probe, deciding when ripe."""

    if device in _reflink_fast:
        return

    bytes_total, ns_total = _reflink_probe.get(device, (0, 0))

    bytes_total += size

    ns_total += elapsed_ns

    if ns_total >= _REFLINK_PROBE_MIN_NS:
        if bytes_total < ns_total * _REFLINK_MIN_BYTES_PER_NS:
            _reflink_slow.add(device)

        else:
            _reflink_fast.add(device)

    elif bytes_total >= _REFLINK_PROVE_FAST_BYTES:
        _reflink_fast.add(device)

    else:
        _reflink_probe[device] = (bytes_total, ns_total)


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
        source_stat = os.fstat(source_fd)

        mode = stat.S_IMODE(source_stat.st_mode)

        device = source_stat.st_dev

        if device in _reflink_slow:
            return False

        probing = (
            source_stat.st_size >= _REFLINK_PROBE_MIN_FILE_BYTES
            and device not in _reflink_fast
        )

        destination_fd = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            mode,
        )

        try:
            if probing:
                started = time.perf_counter_ns()

            fcntl.ioctl(destination_fd, _FICLONE, source_fd)

            if probing:
                _record_reflink_timing(
                    device,
                    source_stat.st_size,
                    time.perf_counter_ns() - started,
                )

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
