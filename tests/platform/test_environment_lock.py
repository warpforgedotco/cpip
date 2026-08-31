"""The environment write lock serializes installers and never blocks installs.

pip's maintainers demonstrated a runnable race when two installers drive one
environment (pypa/pip#8187); the transaction now serializes on an advisory
lock file beside the installed packages. The lock must (a) exclude a second
holder while held, (b) create its directory when absent, and (c) degrade to
running unlocked rather than failing an install it cannot protect.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

from cpip.platform.lock import LOCK_FILE_NAME, environment_write_lock


def test_the_lock_excludes_a_second_holder(tmp_path: Path) -> None:
    order: list[str] = []
    first_holds = threading.Event()
    release_first = threading.Event()

    def first() -> None:
        with environment_write_lock(str(tmp_path)):
            order.append("first-acquired")
            first_holds.set()
            assert release_first.wait(timeout=10)
            order.append("first-releasing")

    def second() -> None:
        assert first_holds.wait(timeout=10)
        with environment_write_lock(str(tmp_path)):
            order.append("second-acquired")

    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    threads[0].start()
    threads[1].start()

    assert first_holds.wait(timeout=10)
    release_first.set()
    for thread in threads:
        thread.join(timeout=10)

    assert order == ["first-acquired", "first-releasing", "second-acquired"]
    assert (tmp_path / LOCK_FILE_NAME).exists()


def test_a_missing_target_directory_is_created(tmp_path: Path) -> None:
    target = tmp_path / "venv" / "lib" / "site-packages"

    with environment_write_lock(str(target)):
        assert target.is_dir()

    assert (target / LOCK_FILE_NAME).exists()


def test_an_unwritable_target_still_runs_the_block(tmp_path: Path) -> None:
    parent = tmp_path / "sealed"
    parent.mkdir()
    os.chmod(parent, 0o500)
    target = parent / "site-packages"

    ran = False
    try:
        with environment_write_lock(str(target)):
            ran = True
    finally:
        os.chmod(parent, 0o700)

    assert ran
