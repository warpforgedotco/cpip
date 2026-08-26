from __future__ import annotations

from cpip.cli.lock import remote_hashed_wheel
from cpip.core.versions import Version


class Candidate:
    name = "demo"
    version = Version("1.2.3")
    source_kind = "wheel"
    source_url = "https://packages.invalid/demo-1.2.3-py3-none-any.whl"
    source_filename = "demo-1.2.3-py3-none-any.whl"
    source_hashes = {"sha256": "abc123"}

    @property
    def path(self) -> str:
        raise AssertionError("the remote hashed wheel must not be materialized")


def test_remote_hashed_wheel_uses_index_facts() -> None:
    candidate = Candidate()

    assert remote_hashed_wheel(candidate) == {
        "name": "demo",
        "version": "1.2.3",
        "wheels": [
            {
                "name": "demo-1.2.3-py3-none-any.whl",
                "url": "https://packages.invalid/demo-1.2.3-py3-none-any.whl",
                "hashes": {"sha256": "abc123"},
            },
        ],
    }


def test_remote_wheel_without_sha256_uses_materialization_fallback() -> None:
    candidate = Candidate()
    candidate.source_hashes = {"sha512": "def456"}

    assert remote_hashed_wheel(candidate) is None
