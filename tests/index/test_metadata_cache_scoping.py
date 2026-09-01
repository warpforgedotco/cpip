"""Persistent metadata caching must be scoped to verifiable artifacts.

Cached dependency metadata never expires, which is sound because a release
behind a hash is immutable. An index that publishes no hashes gives the
fingerprint nothing to pin, so it falls back to the bare URL -- and a mutable
index republishing the same filename would then be served the old metadata
forever. Such candidates must stay out of the persistent cache.
"""

from __future__ import annotations

from types import SimpleNamespace

from kpip.index.candidate_materialization import CandidateMaterializer
from kpip.index.links import Link


def materializer_with_cache(cache: object) -> CandidateMaterializer:
    materializer = CandidateMaterializer()
    materializer.persistent_candidate_metadata_cache = cache  # type: ignore[assignment]
    return materializer


def candidate_for(url: str, hashes: dict[str, str] | None = None) -> SimpleNamespace:
    link = Link.from_url(url, source_url=None, hashes=hashes)
    return SimpleNamespace(link=link, name="demo")


def test_hashless_remote_candidates_stay_out_of_the_persistent_cache() -> None:
    cache = object()
    materializer = materializer_with_cache(cache)
    candidate = candidate_for("https://mutable.invalid/demo-1.0-py3-none-any.whl")

    assert materializer.persistent_metadata_cache_for(candidate) is None  # type: ignore[arg-type]


def test_hash_pinned_candidates_use_the_persistent_cache() -> None:
    cache = object()
    materializer = materializer_with_cache(cache)
    candidate = candidate_for(
        "https://index.invalid/demo-1.0-py3-none-any.whl",
        hashes={"sha256": "0" * 64},
    )

    assert materializer.persistent_metadata_cache_for(candidate) is cache  # type: ignore[arg-type]


def test_local_files_use_their_stat_identity(tmp_path: object) -> None:
    import pathlib

    wheel = pathlib.Path(str(tmp_path)) / "demo-1.0-py3-none-any.whl"
    wheel.write_bytes(b"zip")

    cache = object()
    materializer = materializer_with_cache(cache)
    candidate = candidate_for(wheel.as_uri())

    assert materializer.persistent_metadata_cache_for(candidate) is cache  # type: ignore[arg-type]


def test_no_configured_cache_stays_none() -> None:
    materializer = materializer_with_cache(None)
    candidate = candidate_for(
        "https://index.invalid/demo-1.0-py3-none-any.whl",
        hashes={"sha256": "0" * 64},
    )

    assert materializer.persistent_metadata_cache_for(candidate) is None  # type: ignore[arg-type]
