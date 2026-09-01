"""Contains functional tests of the Bazaar class."""

import os
import sys
import sysconfig
from pathlib import Path

import pytest
from kpip.vcs.bazaar import Bazaar
from kpip.vcs.versioncontrol import RemoteNotFoundError
from kpip_test_support import KpipTestEnvironment, is_bzr_installed, need_bzr


@pytest.mark.skipif(
    sys.platform != "darwin" or "CI" not in os.environ,
    reason="Bazaar is only available under CI on macOS",
)
@pytest.mark.skipif(
    bool(sysconfig.get_config_var("Py_GIL_DISABLED")),
    reason="bzr subprocess aborts under PYTHON_GIL=0 on free-threaded builds",
)
def test_ensure_bzr_available() -> None:
    """Make sure that bzr is available when running in CI."""
    assert is_bzr_installed()


@need_bzr
def test_get_remote_url__no_remote(script: KpipTestEnvironment, tmpdir: Path) -> None:
    repo_dir = tmpdir / "temp-repo"
    repo_dir.mkdir()

    script.run("bzr", "init", os.fspath(repo_dir))

    with pytest.raises(RemoteNotFoundError):
        Bazaar().get_remote_url(os.fspath(repo_dir))
