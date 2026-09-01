"""``reset_caches`` must actually reset, or the benchmarks measure a lie.

Every benchmark calls it before the work it times. A cache it forgets does not
fail anything: the first iteration stays cold, the rest run warm, and the
reported figure drifts toward a steady state no real invocation sees. Worse,
a regression in the cold path becomes invisible.

So rather than list the caches by hand -- the list is exactly what goes stale
-- these tests discover them and assert they are empty afterwards.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest
from benchmark_support import (
    cold_metadata_cache_dir,
    make_wrong_package_graph,
    reset_caches,
)
from kpip.core import names
from kpip.core import packaging
from kpip.core import wheel
from kpip.index import metadata_cache
from kpip.index.provider import CandidateProvider
from kpip.resolution.api import ResolutionEngine

CACHED_MODULES = (names, packaging, wheel)

ENVIRONMENT_DERIVED = frozenset({"kpip.core.wheel.supported_wheel_tags"})


def memoized_callables() -> list[tuple[str, Any]]:
    """Every ``lru_cache``-wrapped callable reachable in those modules."""
    found: list[tuple[str, Any]] = []
    for module in CACHED_MODULES:
        for attribute in dir(module):
            value = getattr(module, attribute, None)
            if hasattr(value, "cache_info") and hasattr(value, "cache_clear"):
                found.append((f"{module.__name__}.{attribute}", value))
                continue
            if isinstance(value, type):
                for inner in dir(value):
                    member = getattr(value, inner, None)
                    if hasattr(member, "cache_info") and hasattr(member, "cache_clear"):
                        found.append(
                            (f"{module.__name__}.{attribute}.{inner}", member),
                        )
    return found


def warm_everything() -> None:
    """Run a real resolution so every cache on the path has entries."""
    with tempfile.TemporaryDirectory() as scratch:
        wheelhouse = Path(scratch) / "wheelhouse"
        wheelhouse.mkdir()
        make_wrong_package_graph(wheelhouse, "warm", versions=4)
        ResolutionEngine(
            provider=CandidateProvider.from_options(
                find_links=[str(wheelhouse)],
                no_index=True,
                wheel_cache_dir=cold_metadata_cache_dir(),
            ),
            ignore_installed=True,
        ).resolve(["warm-root"])


def test_reset_caches_empties_every_memoized_callable() -> None:
    warm_everything()

    populated = [name for name, fn in memoized_callables() if fn.cache_info().currsize]
    assert populated, "the warm-up resolved nothing, so this proves nothing"

    reset_caches()

    still_populated = {
        name: fn.cache_info().currsize
        for name, fn in memoized_callables()
        if fn.cache_info().currsize and name not in ENVIRONMENT_DERIVED
    }
    assert not still_populated, (
        "reset_caches missed these; add them to it so benchmark iterations "
        f"stay cold: {still_populated}"
    )


@pytest.mark.parametrize(
    "name",
    ["wheel_metadata_cache", "wheel_dependency_cache"],
)
def test_reset_caches_empties_module_dictionaries(name: str) -> None:
    warm_everything()
    reset_caches()

    assert not getattr(wheel, name)


def test_reset_caches_drops_persistent_metadata_cache_instances() -> None:
    """These live one per directory per process, outliving their provider."""
    warm_everything()
    assert metadata_cache._CACHE_INSTANCES

    reset_caches()

    assert not metadata_cache._CACHE_INSTANCES


def test_cold_metadata_cache_dir_never_repeats() -> None:
    """A reused directory would make the second iteration a warm one."""
    seen = {cold_metadata_cache_dir() for _ in range(5)}

    assert len(seen) == 5
    assert all(Path(directory).is_dir() for directory in seen)
