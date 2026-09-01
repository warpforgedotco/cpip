from pathlib import Path

import pytest
from kpip.vcs.subversion import Subversion
from kpip.vcs.versioncontrol import RemoteNotFoundError
from kpip_test_support import KpipTestEnvironment, create_svn_repo, need_svn


@need_svn
def test_get_remote_url__no_remote(script: KpipTestEnvironment, tmpdir: Path) -> None:
    repo_path = tmpdir / "temp-repo"
    repo_path.mkdir()
    repo_dir = str(repo_path)

    create_svn_repo(script.scratch_path, repo_dir)

    with pytest.raises(RemoteNotFoundError):
        Subversion().get_remote_url(repo_dir)


@need_svn
def test_get_remote_url__no_remote_with_setup(
    script: KpipTestEnvironment,
    tmpdir: Path,
) -> None:
    repo_path = tmpdir / "temp-repo"
    repo_path.mkdir()
    setup = repo_path / "setup.py"
    setup.touch()
    repo_dir = str(repo_path)

    create_svn_repo(script.scratch_path, repo_dir)

    with pytest.raises(RemoteNotFoundError):
        Subversion().get_remote_url(repo_dir)
