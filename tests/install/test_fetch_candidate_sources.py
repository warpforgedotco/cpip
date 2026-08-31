"""``fetch_candidate_sources`` fetches candidate artifacts concurrently.

``cpip download`` used to bring each artifact local one at a time, so a cold
multi-package download paid one full RTT-plus-transfer per candidate in
sequence. The helper pools the safe fetches; these tests pin the properties
that matter: results keep candidate order, independent fetches overlap, and
a VCS sdist (which may prompt for credentials) stays on the calling thread.
"""

from __future__ import annotations

import threading
from typing import Any

from cpip.install.output import fetch_candidate_sources


class Candidate:
    def __init__(
        self,
        name: str,
        source_kind: str = "wheel",
        source_url: str | None = None,
    ) -> None:
        self.name = name
        self.source_kind = source_kind
        self.source_url = source_url


def test_results_keep_candidate_order() -> None:
    candidates = [Candidate(f"pkg-{index}") for index in range(8)]

    fetched = fetch_candidate_sources(candidates, lambda candidate: candidate.name)

    assert fetched == [candidate.name for candidate in candidates]


def test_independent_fetches_overlap() -> None:
    """Two fetches must be in flight at once for either to finish."""
    barrier = threading.Barrier(2, timeout=10)

    def fetch(candidate: Any) -> str:
        barrier.wait()
        return candidate.name

    fetched = fetch_candidate_sources([Candidate("a"), Candidate("b")], fetch)

    assert fetched == ["a", "b"]


def test_vcs_sdist_fetches_on_the_calling_thread() -> None:
    """A VCS fetch may prompt for credentials, so it must not enter the pool."""
    threads: dict[str, str] = {}

    def fetch(candidate: Any) -> str:
        threads[candidate.name] = threading.current_thread().name
        return candidate.name

    candidates = [
        Candidate("wheel-a"),
        Candidate("vcs", source_kind="sdist", source_url="git+https://x/y.git"),
        Candidate("wheel-b"),
    ]

    fetched = fetch_candidate_sources(candidates, fetch)

    assert fetched == ["wheel-a", "vcs", "wheel-b"]
    assert threads["vcs"] == threading.current_thread().name
    assert threads["wheel-a"] != threading.current_thread().name


def test_ordinary_sdist_url_still_pools() -> None:
    barrier = threading.Barrier(2, timeout=10)

    def fetch(candidate: Any) -> str:
        barrier.wait()
        return candidate.name

    candidates = [
        Candidate("sdist", source_kind="sdist", source_url="https://x/y.tar.gz"),
        Candidate("wheel"),
    ]

    assert fetch_candidate_sources(candidates, fetch) == ["sdist", "wheel"]
