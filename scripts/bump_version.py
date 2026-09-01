"""Bump kpip's release version through uv and keep the source literal in sync.

Usage::

    uv run scripts/bump_version.py 0.0.2
    uv run scripts/bump_version.py --bump patch
    uv run scripts/bump_version.py --bump patch --bump alpha

``uv version`` owns ``pyproject.toml`` and ``uv.lock``.  kpip also keeps a
literal ``__version__`` for cheap source-tree startup, so this script updates
that one additional location after uv succeeds.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = Path("src/kpip/__init__.py")

_VERSION_ASSIGNMENT = re.compile(
    r"^(?P<prefix>__version__\s*=\s*)(?P<quote>['\"])(?P<version>[^'\"]+)"
    r"(?P=quote)(?P<suffix>[ \t]*\r?)$",
    re.MULTILINE,
)


class VersionBumpError(RuntimeError):
    """The version sources could not be updated safely."""


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description=(
            "Update kpip's project version with `uv version`, then synchronize "
            "src/kpip/__init__.py."
        ),
    )
    result.add_argument("version", nargs="?", help="exact PEP 440 version")
    result.add_argument(
        "--bump",
        action="append",
        metavar="COMPONENT[=VALUE]",
        help=(
            "uv version component to bump; may be repeated "
            "(major, minor, patch, stable, alpha, beta, rc, post, or dev)"
        ),
    )
    result.add_argument(
        "--dry-run",
        action="store_true",
        help="show the version uv would select without changing files",
    )
    result.add_argument(
        "--uv",
        default="uv",
        metavar="PATH",
        help="uv executable to invoke (default: uv)",
    )
    return result


def version_literal(text: str) -> str:
    matches = list(_VERSION_ASSIGNMENT.finditer(text))
    if len(matches) != 1:
        raise VersionBumpError(
            "expected exactly one __version__ assignment in src/kpip/__init__.py",
        )
    return matches[0].group("version")


def replace_version_literal(text: str, expected: str, replacement: str) -> str:
    matches = list(_VERSION_ASSIGNMENT.finditer(text))
    if len(matches) != 1:
        raise VersionBumpError(
            "expected exactly one __version__ assignment in src/kpip/__init__.py",
        )
    match = matches[0]
    current = match.group("version")
    if current != expected:
        raise VersionBumpError(
            f"source version is {current!r}, but uv reports {expected!r}",
        )
    start, end = match.span("version")
    return text[:start] + replacement + text[end:]


def read_text_preserving_newlines(path: Path) -> str:
    with path.open(encoding="utf-8", newline="") as stream:
        return stream.read()


def atomic_write_text(path: Path, text: str) -> None:
    mode = stat.S_IMODE(path.stat().st_mode)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        text=True,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def uv_version(
    uv: str,
    arguments: list[str],
    *,
    root: Path = ROOT,
) -> dict[str, Any]:
    command = [uv, "version", *arguments, "--output-format", "json"]
    try:
        process = subprocess.run(
            command,
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise VersionBumpError(f"could not execute {uv!r}: {exc}") from exc

    if process.stderr:
        print(process.stderr, file=sys.stderr, end="")
    if process.returncode:
        if process.stdout:
            print(process.stdout, file=sys.stderr, end="")
        raise VersionBumpError(f"uv version exited with status {process.returncode}")

    try:
        payload = json.loads(process.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise VersionBumpError("uv version returned invalid JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("version"), str):
        raise VersionBumpError("uv version did not return a project version")
    return payload


def run(arguments: argparse.Namespace, *, root: Path = ROOT) -> None:
    if bool(arguments.version) == bool(arguments.bump):
        raise VersionBumpError("provide either an exact VERSION or at least one --bump")

    version_path = root / VERSION_FILE
    source_text = read_text_preserving_newlines(version_path)
    source_version = version_literal(source_text)

    current = uv_version(arguments.uv, [], root=root)
    old_version = current["version"]
    if source_version != old_version:
        raise VersionBumpError(
            f"source version is {source_version!r}, but uv reports {old_version!r}",
        )

    update_arguments = [arguments.version] if arguments.version else []
    for bump in arguments.bump or ():
        update_arguments.extend(("--bump", bump))
    if arguments.dry_run:
        update_arguments.append("--dry-run")
    else:
        # Re-lock, but do not sync an environment while __version__ still has
        # the old value. The outer `uv run` already prepared the script's env.
        update_arguments.append("--no-sync")

    updated = uv_version(arguments.uv, update_arguments, root=root)
    new_version = updated["version"]
    package_name = updated.get("package_name") or current.get("package_name") or "kpip"
    suffix = " (dry run)" if arguments.dry_run else ""
    print(f"{package_name} {old_version} => {new_version}{suffix}")

    if arguments.dry_run or new_version == old_version:
        return

    updated_source = replace_version_literal(source_text, old_version, new_version)
    atomic_write_text(version_path, updated_source)
    print(f"Synchronized {VERSION_FILE}")


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    try:
        run(arguments)
    except VersionBumpError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
