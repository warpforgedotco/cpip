"""The environment write lock serializes installers without touching the target.

pip's maintainers demonstrated a runnable race when two installers drive one
environment (pypa/pip#8187); the transaction now serializes on an advisory
lock keyed by the target path. The lock must (a) exclude a second holder
while held, (b) leave the target completely untouched -- a failed install
must leave an absent target absent, and installed trees must not grow
bookkeeping files -- and (c) degrade to running unlocked rather than failing
an install it cannot protect.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest
from cpip.platform import lock
from cpip.platform.lock import environment_write_lock, lock_path_for


def test_the_lock_excludes_a_second_holder(tmp_path: Path) -> None:
    target = str(tmp_path / "site-packages")
    order: list[str] = []
    first_holds = threading.Event()
    release_first = threading.Event()

    def first() -> None:
        with environment_write_lock(target):
            order.append("first-acquired")
            first_holds.set()
            assert release_first.wait(timeout=10)
            order.append("first-releasing")

    def second() -> None:
        assert first_holds.wait(timeout=10)
        with environment_write_lock(target):
            order.append("second-acquired")

    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    threads[0].start()
    threads[1].start()

    assert first_holds.wait(timeout=10)
    release_first.set()
    for thread in threads:
        thread.join(timeout=10)

    assert order == ["first-acquired", "first-releasing", "second-acquired"]


def test_the_target_is_never_touched(tmp_path: Path) -> None:
    target = tmp_path / "venv" / "lib" / "site-packages"

    with environment_write_lock(str(target)):
        assert not target.exists()

    assert not target.parent.exists()
    assert os.path.exists(lock_path_for(str(target)))


def test_the_lock_path_is_stable_per_target(tmp_path: Path) -> None:
    target = str(tmp_path / "site-packages")
    other = str(tmp_path / "elsewhere")

    assert lock_path_for(target) == lock_path_for(target)
    assert lock_path_for(target) != lock_path_for(other)


def test_an_uncreatable_lock_still_runs_the_block(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(*args: object, **kwargs: object) -> int:
        raise OSError("no locks here")

    monkeypatch.setattr(lock.os, "open", refuse)

    ran = False
    with environment_write_lock(str(tmp_path / "site-packages")):
        ran = True

    assert ran
