"""Rules the wheel format states that the installer used to ignore.

``Root-Is-Purelib`` was written into generated wheels and never read back, so
every wheel landed in purelib; ``direct_url.json`` lost its ``subdirectory``
on a round trip; and only the exact bytes ``#!python\\n`` were recognised as a
pseudo-shebang, missing ``#!pythonw`` and the CRLF form.
"""

from __future__ import annotations

import pytest
from cpip.core.direct_url import ArchiveInfo, DirectUrl
from cpip.core.errors import UnsupportedWheel
from cpip.core.wheel import root_is_purelib_from_text
from cpip.install.wheel_archive import destination_internal_parts_text
from cpip.install.wheel_scripts import rewrite_shebang


class _SplitTarget:
    """A scheme whose purelib and platlib differ, as system layouts do."""

    purelib = "/usr/lib/python3/site-packages"
    platlib = "/usr/lib64/python3/site-packages"
    scripts = "/usr/bin"
    data = "/usr"
    headers = "/usr/include/python3"
    resolved_roots_internal = None


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Wheel-Version: 1.0\nRoot-Is-Purelib: true\n", True),
        ("Wheel-Version: 1.0\nRoot-Is-Purelib: false\n", False),
        ("Wheel-Version: 1.0\nRoot-Is-Purelib: True\n", True),
        ("Wheel-Version: 1.0\nRoot-Is-Purelib: FALSE\n", False),
        ("Wheel-Version: 1.0\nRoot-Is-Purelib:   true  \n", True),
        # Anything that is not `true` reads as false.
        ("Wheel-Version: 1.0\nRoot-Is-Purelib: yes\n", False),
    ],
)
def test_root_is_purelib_from_text(text: str, expected: bool) -> None:
    assert root_is_purelib_from_text(text) is expected


def test_root_is_purelib_is_required() -> None:
    with pytest.raises(UnsupportedWheel):
        root_is_purelib_from_text("Wheel-Version: 1.0\nTag: py3-none-any\n")


def test_wheel_root_follows_root_is_purelib() -> None:
    target = _SplitTarget()
    assert (
        destination_internal_parts_text(
            target,
            ("pkg", "mod.py"),
            "pkg/mod.py",
            root_is_purelib=True,
        )
        == "/usr/lib/python3/site-packages/pkg/mod.py"
    )
    assert (
        destination_internal_parts_text(
            target,
            ("pkg", "_speedups.so"),
            "pkg/_speedups.so",
            root_is_purelib=False,
        )
        == "/usr/lib64/python3/site-packages/pkg/_speedups.so"
    )


@pytest.mark.parametrize("root_is_purelib", [True, False])
def test_data_directory_is_unaffected(root_is_purelib: bool) -> None:
    """`.data` entries name their own scheme key, whatever the root is."""
    target = _SplitTarget()
    assert (
        destination_internal_parts_text(
            target,
            ("pkg-1.0.data", "scripts", "tool"),
            "pkg-1.0.data/scripts/tool",
            root_is_purelib=root_is_purelib,
        )
        == "/usr/bin/tool"
    )
    assert (
        destination_internal_parts_text(
            target,
            ("pkg-1.0.data", "platlib", "ext.so"),
            "pkg-1.0.data/platlib/ext.so",
            root_is_purelib=root_is_purelib,
        )
        == "/usr/lib64/python3/site-packages/ext.so"
    )


def test_direct_url_round_trips_subdirectory() -> None:
    """A package built from a monorepo subdirectory must record which one."""
    data = {"url": "file:///src", "subdirectory": "project", "dir_info": {}}
    parsed = DirectUrl.from_dict(data)
    assert parsed.subdirectory == "project"
    assert parsed.to_dict() == data


def test_direct_url_omits_subdirectory_when_absent() -> None:
    parsed = DirectUrl.from_dict({"url": "file:///src", "dir_info": {}})
    assert parsed.subdirectory is None
    assert "subdirectory" not in parsed.to_dict()


def test_direct_url_compat_keeps_hashes_alongside_hash() -> None:
    """The spec says producers SHOULD emit `hashes`; `hash` is the legacy
    spelling kept for older readers, not a replacement for it."""
    direct = DirectUrl(
        url="https://example.invalid/x.whl",
        archive_info=ArchiveInfo(hashes={"sha256": "abc", "md5": "def"}),
    )
    archive_info = direct.to_dict_compat()["archive_info"]
    assert archive_info["hashes"] == {"sha256": "abc", "md5": "def"}
    assert archive_info["hash"] == "sha256=abc"


@pytest.mark.parametrize(
    "contents, expected",
    [
        (b"#!python\nbody\n", b"#!/opt/py/bin/python3\nbody\n"),
        # A wheel built on Windows carries CRLF.
        (b"#!python\r\nbody\r\n", b"#!/opt/py/bin/python3\nbody\r\n"),
        # The format explicitly allows the pythonw convention.
        (b"#!pythonw\nbody\n", b"#!/opt/py/bin/python3\nbody\n"),
        (b"#!python -X utf8\nbody\n", b"#!/opt/py/bin/python3\nbody\n"),
        (b"#!python", b"#!/opt/py/bin/python3\n"),
        # Anything else is the script's own shebang and is left alone.
        (b"#!/bin/sh\necho hi\n", b"#!/bin/sh\necho hi\n"),
        (b"#!/usr/bin/env python\nbody\n", b"#!/usr/bin/env python\nbody\n"),
        (b"no shebang at all\n", b"no shebang at all\n"),
    ],
)
def test_rewrite_shebang(tmp_path, contents: bytes, expected: bytes) -> None:
    script = tmp_path / "tool"
    script.write_bytes(contents)
    rewrite_shebang(str(script), "/opt/py/bin/python3")
    assert script.read_bytes() == expected
