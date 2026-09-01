from __future__ import annotations

from argparse import Namespace
from pathlib import Path

import pytest

from scripts import bump_version


def project(root: Path, version: str = "0.0.1") -> Path:
    source = root / bump_version.VERSION_FILE
    source.parent.mkdir(parents=True)
    source.write_text(
        f'from __future__ import annotations\n\n__version__ = "{version}"\n',
        encoding="utf-8",
    )
    return source


def arguments(*, dry_run: bool = False) -> Namespace:
    return Namespace(
        version=None,
        bump=["patch"],
        dry_run=dry_run,
        uv="uv",
    )


@pytest.mark.parametrize(
    "dry_run, update_arguments, expected_version",
    [
        (False, ["--bump", "patch", "--no-sync"], "0.0.2"),
        (True, ["--bump", "patch", "--dry-run"], "0.0.1"),
    ],
)
def test_run_delegates_to_uv_and_synchronizes_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    dry_run: bool,
    update_arguments: list[str],
    expected_version: str,
) -> None:
    source = project(tmp_path)
    calls: list[list[str]] = []

    def fake_uv_version(
        _uv: str,
        uv_arguments: list[str],
        *,
        root: Path,
    ) -> dict[str, object]:
        assert root == tmp_path
        calls.append(uv_arguments)
        version = "0.0.1" if not uv_arguments else "0.0.2"
        return {"package_name": "kpip", "version": version}

    monkeypatch.setattr(bump_version, "uv_version", fake_uv_version)

    bump_version.run(arguments(dry_run=dry_run), root=tmp_path)

    assert calls == [[], update_arguments]
    assert bump_version.version_literal(source.read_text()) == expected_version


def test_run_refuses_mismatched_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project(tmp_path, version="0.0.0")
    monkeypatch.setattr(
        bump_version,
        "uv_version",
        lambda *_args, **_kwargs: {"package_name": "kpip", "version": "0.0.1"},
    )

    with pytest.raises(bump_version.VersionBumpError, match="source version"):
        bump_version.run(arguments(), root=tmp_path)
