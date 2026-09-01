from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

import pytest

from kpip.build.build import unpack_source
from kpip.core.errors import BuildError


def test_unpack_source_rejects_zip_path_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "project.zip"
    destination = tmp_path / "unpacked"
    destination.mkdir()
    with zipfile.ZipFile(archive, "w") as zip_file:
        zip_file.writestr("../escaped.txt", "untrusted")

    with pytest.raises(
        BuildError, match="outside (?:target directory|the destination)"
    ):
        unpack_source(str(archive), str(destination))

    assert not (tmp_path / "escaped.txt").exists()


def test_unpack_source_rejects_tar_symlink_escape(tmp_path: Path) -> None:
    archive = tmp_path / "project.tar"
    destination = tmp_path / "unpacked"
    destination.mkdir()
    with tarfile.open(archive, "w") as tar_file:
        symlink = tarfile.TarInfo("project/link")
        symlink.type = tarfile.SYMTYPE
        symlink.linkname = "../../escaped"
        tar_file.addfile(symlink)

        payload = b"untrusted"
        member = tarfile.TarInfo("project/link/payload.txt")
        member.size = len(payload)
        tar_file.addfile(member, io.BytesIO(payload))

    with pytest.raises(
        BuildError, match="outside (?:target directory|the destination)"
    ):
        unpack_source(str(archive), str(destination))

    assert not (tmp_path / "escaped" / "payload.txt").exists()


@pytest.mark.parametrize("extension", ["zip", "tar"])
def test_unpack_source_preserves_single_project_root(
    tmp_path: Path,
    extension: str,
) -> None:
    archive = tmp_path / f"project.{extension}"
    destination = tmp_path / "unpacked"
    destination.mkdir()
    if extension == "zip":
        with zipfile.ZipFile(archive, "w") as zip_file:
            zip_file.writestr("project-1.0/pyproject.toml", "[project]")
    else:
        payload = b"[project]"
        with tarfile.open(archive, "w") as tar_file:
            member = tarfile.TarInfo("project-1.0/pyproject.toml")
            member.size = len(payload)
            tar_file.addfile(member, io.BytesIO(payload))

    project_root = Path(unpack_source(str(archive), str(destination)))

    assert project_root == destination / "project-1.0"
    assert (project_root / "pyproject.toml").read_bytes() == b"[project]"
