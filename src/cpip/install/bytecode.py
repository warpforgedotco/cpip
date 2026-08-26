"""Byte-compilation across worker processes.

Compiling is the dominant cost of filling the archive cache -- for a sixteen
wheel set it was measured at 2.5s of a 2.7s fill -- and ``compile()`` holds
the GIL, so the thread pool that extracts wheels cannot overlap any of it.

The work therefore goes to child interpreters running
:mod:`cpip.install._compile_worker`. They are started once and reused for
every module in the session, because starting an interpreter costs about as
much as compiling a small module.

Everything here is optional. If workers cannot be started, misbehave, or
time out, the caller compiles in-process instead: this makes installs
faster, it is never the reason one fails.
"""

from __future__ import annotations

import os
import sys
import threading
from typing import TYPE_CHECKING

from cpip.core.utils import default_worker_count

if TYPE_CHECKING:
    import subprocess
    from collections.abc import Iterable, Iterator
    from typing import IO

CompileJob = tuple[str, str, str]
"""A module to compile: source path, ``.pyc`` path, and the name to record
inside the code object."""

MAX_WORKERS = 4
"""Worker processes to run.

Measured on the benchmark set: four processes compile it in 629ms against
1536ms serially, and eight in 694ms. Past four, starting interpreters costs
more than the parallelism returns.
"""

STARTUP_TIMEOUT = 30.0

COMPILE_TIMEOUT = 60.0
"""Longer than any single module should ever take."""

_READY = "Ready"


class _Worker:
    """One child interpreter, and the pipe it answers on."""

    __slots__ = ("process",)

    def __init__(self, process: subprocess.Popen[str]) -> None:
        self.process = process

    def compile(self, job: CompileJob) -> bool:
        """Compile one module. Returns ``False`` if this worker is unusable."""
        source, destination, display = job

        stdin = self.process.stdin

        stdout = self.process.stdout

        if stdin is None or stdout is None:
            return False

        try:
            stdin.write(f"{source}\t{destination}\t{display}\n")

            stdin.flush()

        except (BrokenPipeError, OSError, ValueError):
            return False

        # Exactly one line is in flight per worker and it is consumed here, so
        # the reader is never left holding buffered bytes that `select` cannot
        # see. A worker that writes anything extra fails the echo check below.
        if not _wait_readable(stdout, COMPILE_TIMEOUT):
            return False

        try:
            echoed = stdout.readline()

        except (OSError, ValueError):
            return False

        # The worker echoes the path it was handed. Anything else means we are
        # not talking to the script we think we are, so stop trusting it.
        return echoed.rstrip("\n") == source

    def close(self) -> None:
        process = self.process

        for stream in (process.stdin, process.stdout):
            try:
                if stream is not None:
                    stream.close()

            except OSError:
                pass

        try:
            process.wait(timeout=5)

        except Exception:
            process.kill()


def _wait_readable(stream: IO[str], timeout: float) -> bool:
    """Whether ``stream`` has a line waiting within ``timeout`` seconds.

    A worker that wedges must not wedge the install with it.
    """
    import select

    try:
        readable, _, _ = select.select([stream], [], [], timeout)

    except (OSError, ValueError):
        return False

    return bool(readable)


def _worker_script() -> str:
    return os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "_compile_worker.py"
    )


def _spawn() -> _Worker | None:
    """Start one worker, or ``None`` if it will not answer."""
    import subprocess

    try:
        process = subprocess.Popen(  # noqa: S603
            [sys.executable, _worker_script()],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            close_fds=True,
        )

    except (OSError, ValueError):
        return None

    worker = _Worker(process)

    stdout = process.stdout

    if stdout is None or not _wait_readable(stdout, STARTUP_TIMEOUT):
        worker.close()

        return None

    try:
        greeting = stdout.readline()

    except (OSError, ValueError):
        worker.close()

        return None

    if greeting.rstrip("\n") != _READY:
        worker.close()

        return None

    return worker


class CompilePool:
    """A set of worker processes, started on first use and reused after.

    One batch compiles at a time. A batch already spreads itself across every
    worker, so admitting two would only make them contend for the same four
    children; wheels extracted concurrently queue here instead, and each one
    still gets the whole pool while it holds it.
    """

    def __init__(self, workers: int) -> None:
        self._limit = max(1, min(workers, default_worker_count()))

        self._workers: list[_Worker] = []

        self._lock = threading.Lock()

        self._started = False

        self._broken = False

    def _ensure_started(self) -> bool:
        if self._started:
            return not self._broken

        self._started = True

        for _ in range(self._limit):
            worker = _spawn()

            if worker is None:
                break

            self._workers.append(worker)

        self._broken = not self._workers

        return not self._broken

    def compile(self, jobs: Iterable[CompileJob]) -> list[CompileJob] | None:
        """Compile ``jobs``, returning the ones a worker could not take.

        ``None`` means no worker was available at all and the whole batch is
        the caller's to compile.
        """
        pending: list[CompileJob] = []

        rejected: list[CompileJob] = []

        for job in jobs:
            (pending if _is_transmittable(job) else rejected).append(job)

        if not pending:
            return rejected

        with self._lock:
            if not self._ensure_started():
                return None

            rejected.extend(self._run(pending))

        return rejected

    def _run(self, jobs: list[CompileJob]) -> list[CompileJob]:
        """Feed ``jobs`` to every worker at once; return the ones that failed.

        One feeder thread per worker, all pulling from a single iterator, so a
        worker that draws a slow module does not hold up the others and the
        batch finishes when the work does rather than when the slowest even
        share does.
        """
        source = iter(jobs)

        guard = threading.Lock()

        failed: list[CompileJob] = []

        def take() -> Iterator[CompileJob]:
            while True:
                with guard:
                    job = next(source, None)

                if job is None:
                    return

                yield job

        def feed(worker: _Worker) -> None:
            local_failures: list[CompileJob] = []

            for job in take():
                if not worker.compile(job):
                    local_failures.append(job)

                    # A worker that breaks the protocol will keep breaking it.
                    local_failures.extend(take())

                    self._broken = True

                    break

            if local_failures:
                with guard:
                    failed.extend(local_failures)

        threads = [
            threading.Thread(
                target=feed,
                args=(worker,),
                name="cpip-compile",
                daemon=True,
            )
            for worker in self._workers
        ]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join()

        if self._broken:
            self._shutdown()

        return failed

    def _shutdown(self) -> None:
        for worker in self._workers:
            worker.close()

        self._workers.clear()

    def close(self) -> None:
        with self._lock:
            self._shutdown()

            self._broken = True


def _is_transmittable(job: CompileJob) -> bool:
    """Whether ``job`` survives a tab-separated line.

    Paths holding a tab or a newline would be misparsed by the worker; they
    are rare enough to just compile in-process.
    """
    return not any(character in field for field in job for character in "\t\r\n")


_POOL: CompilePool | None = None

_POOL_LOCK = threading.Lock()


def compile_jobs(jobs: list[CompileJob]) -> list[CompileJob]:
    """Compile ``jobs`` across worker processes.

    Returns the jobs no worker took, for the caller to compile in-process.
    """
    global _POOL

    if not jobs:
        return []

    with _POOL_LOCK:
        if _POOL is None:
            import atexit

            _POOL = CompilePool(MAX_WORKERS)

            atexit.register(shutdown)

        pool = _POOL

    remaining = pool.compile(jobs)

    return jobs if remaining is None else remaining


def shutdown() -> None:
    """Stop the workers. Safe to call more than once."""
    global _POOL

    with _POOL_LOCK:
        pool, _POOL = _POOL, None

    if pool is not None:
        pool.close()
