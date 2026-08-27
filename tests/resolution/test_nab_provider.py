from __future__ import annotations

from types import SimpleNamespace

import pytest

import cpip.resolution.nab_provider
from cpip._vendor.nab_resolver.ranges import Range
from cpip.core.packaging import parse_requirement
from cpip.core.versions import Version
from cpip.index.provider import CandidateProvider
from cpip.resolution.models import ResolutionConfig
from cpip.resolution.nab_provider import NabProvider


class FakeProvider:
    def __init__(self) -> None:
        self.available_calls = 0
        self.events: list[tuple[str, tuple[str, ...] | str]] = []
        self.prefetch_calls: list[tuple[str, ...]] = []
        self.versions = {
            "app": (Version("1"),),
            "dep": (Version("1"), Version("2")),
        }
        self.candidates = {
            ("app", Version("1")): SimpleNamespace(
                name="app",
                canonical_name="app",
                version=Version("1"),
                dependencies=(parse_requirement("dep<2"),),
                source_url="file:///app.whl",
                source_kind="wheel",
            ),
            ("dep", Version("1")): SimpleNamespace(
                name="dep",
                canonical_name="dep",
                version=Version("1"),
                dependencies=(),
                source_url="file:///dep-1.whl",
                source_kind="wheel",
            ),
            ("dep", Version("2")): SimpleNamespace(
                name="dep",
                canonical_name="dep",
                version=Version("2"),
                dependencies=(),
                source_url="file:///dep-2.whl",
                source_kind="wheel",
            ),
        }

    def available_versions(self, requirement):
        self.available_calls += 1
        return tuple(
            SimpleNamespace(version=version)
            for version in self.versions[requirement.name]
        )

    def prefetch_available_versions(self, requirements):
        names = tuple(requirement.canonical_name for requirement in requirements)
        self.prefetch_calls.append(names)
        self.events.append(("prefetch", names))

    def find_candidates(self, requirement, *, allowed_versions=None):
        self.events.append(("find", requirement.canonical_name))
        if allowed_versions is None:
            return tuple(
                candidate
                for (name, _), candidate in self.candidates.items()
                if name == requirement.name
            )
        return (self.candidates[(requirement.name, next(iter(allowed_versions)))],)


class IndexedFakeProvider(CandidateProvider):
    def __init__(self) -> None:
        self.candidate = SimpleNamespace(
            name="app",
            canonical_name="app",
            version=Version("1"),
            dependencies=(),
            source_url="file:///app.whl",
            source_kind="wheel",
        )
        self.release_calls = 0

    def available_versions(self, requirement):
        return (SimpleNamespace(version=Version("1")),)

    def find_candidates(self, requirement, *, allowed_versions):
        raise AssertionError("indexed releases should not rescan the catalog")

    def release_candidates(self, requirement, version):
        self.release_calls += 1
        return (self.candidate,)

    def get_materializer_internal(self):
        return SimpleNamespace(materialize=lambda requirement, records: records)


def test_constraints_are_indexed_once(monkeypatch: pytest.MonkeyPatch) -> None:
    marker_calls = 0
    marker_applies = cpip.resolution.nab_provider.marker_applies

    def counting_marker_applies(marker, extras=()):
        nonlocal marker_calls
        marker_calls += 1
        return marker_applies(marker, extras=extras)

    monkeypatch.setattr(
        cpip.resolution.nab_provider,
        "marker_applies",
        counting_marker_applies,
    )
    adapter = NabProvider(
        FakeProvider(),
        ResolutionConfig(
            constraints=("dep==1", "ignored==1; python_version < '0'"),
        ),
    )

    assert adapter._constraint_for("dep") == (parse_requirement("dep==1"),)
    assert adapter._constraint_for("dep[extra]") == (parse_requirement("dep==1"),)
    assert adapter._constraint_for("unrelated") == ()
    assert marker_calls == 2


def test_empty_constraint_lookup_skips_name_normalization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = NabProvider(FakeProvider(), ResolutionConfig())

    def fail_normalization(_name: str) -> str:
        raise AssertionError("empty constraint maps do not need name normalization")

    monkeypatch.setattr(
        cpip.resolution.nab_provider,
        "canonicalize_name",
        fail_normalization,
    )

    assert adapter._constraint_for("Dep_Pkg[extra]") == ()


def test_selected_release_materializes_without_catalog_rescan() -> None:
    provider = IndexedFakeProvider()
    adapter = NabProvider(provider, ResolutionConfig())

    package, version_range = adapter.add_root(parse_requirement("app"))

    assert adapter.choose_version(package, version_range) == Version("1")
    assert provider.release_calls == 1


def test_choose_version_does_not_iterate_empty_constraints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class EmptyConstraints(tuple):
        def __iter__(self):
            raise AssertionError("empty constraints do not need filtering")

    adapter = NabProvider(
        FakeProvider(),
        ResolutionConfig(ignore_installed=True),
    )
    package, version_range = adapter.add_root(parse_requirement("app"))
    monkeypatch.setattr(
        adapter,
        "_constraint_for",
        lambda _package: EmptyConstraints(),
    )

    assert adapter.choose_version(package, version_range) == Version("1")


def test_constraints_apply_to_transitive_dependencies() -> None:
    adapter = NabProvider(FakeProvider(), ResolutionConfig(constraints=("dep==2",)))
    root, root_range = adapter.add_root(parse_requirement("app"))

    assert root == "app"
    assert adapter.choose_version(root, root_range) == Version("1")
    dependencies = adapter.get_dependencies(root, Version("1"))

    assert not adapter.has_satisfying_version("dep", dependencies["dep"])


def test_dependency_constraints_are_looked_up_once_per_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = NabProvider(FakeProvider(), ResolutionConfig(ignore_installed=True))
    root, root_range = adapter.add_root(parse_requirement("app"))
    adapter.choose_version(root, root_range)
    monkeypatch.setattr(adapter, "_prefetch_available_versions", lambda _: None)
    lookups = []
    constraint_for = adapter._constraint_for

    def count_lookup(package: str):
        lookups.append(package)
        return constraint_for(package)

    monkeypatch.setattr(adapter, "_constraint_for", count_lookup)

    adapter.get_dependencies(root, Version("1"))

    assert lookups == ["dep"]


def test_exact_transitive_dependency_skips_catalog_enumeration() -> None:
    provider = FakeProvider()
    provider.candidates[("app", Version("1"))].dependencies = (
        parse_requirement("dep==2"),
    )
    adapter = NabProvider(provider, ResolutionConfig(constraints=("dep!=2",)))
    root, root_range = adapter.add_root(parse_requirement("app"))
    adapter.choose_version(root, root_range)
    provider.available_calls = 0

    dependencies = adapter.get_dependencies(root, Version("1"))

    assert dependencies["dep"] == Range.singleton(Version("2"))
    assert provider.available_calls == 0
    assert adapter.choose_version("dep", dependencies["dep"]) is None


def test_missing_exact_dependency_is_validated_when_selected() -> None:
    provider = FakeProvider()
    provider.candidates[("app", Version("1"))].dependencies = (
        parse_requirement("dep==3"),
    )
    adapter = NabProvider(provider, ResolutionConfig())
    root, root_range = adapter.add_root(parse_requirement("app"))
    adapter.choose_version(root, root_range)
    provider.available_calls = 0

    dependencies = adapter.get_dependencies(root, Version("1"))

    assert dependencies["dep"] == Range.singleton(Version("3"))
    assert provider.available_calls == 0
    assert adapter.choose_version("dep", dependencies["dep"]) is None
    assert provider.available_calls == 1
    assert ("dep", Version("3")) not in adapter.records


def test_arbitrary_equality_dependency_still_enumerates_catalog() -> None:
    provider = FakeProvider()
    provider.candidates[("app", Version("1"))].dependencies = (
        parse_requirement("dep===2"),
    )
    adapter = NabProvider(provider, ResolutionConfig())
    root, root_range = adapter.add_root(parse_requirement("app"))
    adapter.choose_version(root, root_range)
    provider.available_calls = 0

    adapter.get_dependencies(root, Version("1"))

    assert provider.available_calls == 1


def test_no_deps_does_not_expand_selected_candidate() -> None:
    adapter = NabProvider(FakeProvider(), ResolutionConfig(no_deps=True))
    root, root_range = adapter.add_root(parse_requirement("app"))

    assert adapter.choose_version(root, root_range) == Version("1")
    assert adapter.get_dependencies(root, Version("1")) == {}


def test_has_satisfying_version_applies_requirement_and_constraints() -> None:
    adapter = NabProvider(FakeProvider(), ResolutionConfig(constraints=("dep==2",)))
    package, _ = adapter.add_root(parse_requirement("dep<2"))

    assert not adapter.has_satisfying_version(
        package, adapter._finite_range((Version("1"), Version("2")))
    )


def test_version_discovery_is_cached_for_same_requirement_state() -> None:
    provider = FakeProvider()
    adapter = NabProvider(provider, ResolutionConfig())
    package, _ = adapter.add_root(parse_requirement("dep"))

    assert adapter._versions(package) == adapter._versions(package)
    assert provider.available_calls == 1


def test_dependency_markers_are_filtered_at_adapter_boundary() -> None:
    provider = FakeProvider()
    provider.candidates[("app", Version("1"))].dependencies = (
        parse_requirement("dep<2; python_version < '0'"),
    )
    adapter = NabProvider(provider, ResolutionConfig())
    root, root_range = adapter.add_root(parse_requirement("app"))

    adapter.choose_version(root, root_range)

    assert adapter.get_dependencies(root, Version("1")) == {}


def test_add_roots_prefetches_without_materializing_candidates() -> None:
    provider = FakeProvider()
    adapter = NabProvider(provider, ResolutionConfig())

    adapter.add_roots([parse_requirement("app"), parse_requirement("dep")])

    assert provider.prefetch_calls[0] == ("app", "dep")
    assert provider.events == [("prefetch", ("app", "dep"))]


def test_unique_roots_do_not_intersect_their_ranges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = NabProvider(FakeProvider(), ResolutionConfig())

    def fail_intersection(_left, _right):
        raise AssertionError("a unique root has no existing range to intersect")

    monkeypatch.setattr(Range, "__and__", fail_intersection)

    roots = adapter.add_roots([parse_requirement("app"), parse_requirement("dep")])

    assert set(roots) == {"app", "dep"}


def test_duplicate_root_ranges_are_still_intersected() -> None:
    adapter = NabProvider(FakeProvider(), ResolutionConfig())

    roots = adapter.add_roots([parse_requirement("dep<2"), parse_requirement("dep>=2")])

    assert roots["dep"].is_empty


def test_conflicting_direct_url_roots_are_empty() -> None:
    adapter = NabProvider(FakeProvider(), ResolutionConfig())

    roots = adapter.add_roots(
        [
            parse_requirement("dep @ https://example.invalid/dep.whl"),
            parse_requirement("dep @ https://mirror.invalid/dep.whl"),
        ]
    )

    assert roots["dep"].is_empty
    assert adapter.requirements["dep"].url == "https://example.invalid/dep.whl"


def test_equivalent_direct_url_roots_remain_satisfiable() -> None:
    adapter = NabProvider(FakeProvider(), ResolutionConfig())

    roots = adapter.add_roots(
        [
            parse_requirement("dep @ https://example.invalid/dep.whl?b=2&a=1"),
            parse_requirement("dep @ https://example.invalid/dep.whl?a=1&b=2"),
        ]
    )

    assert not roots["dep"].is_empty


def test_dependency_prefetch_filters_unusable_catalog_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = FakeProvider()
    provider.candidates[("app", Version("1"))].dependencies = (
        parse_requirement("dep<2"),
        parse_requirement("other"),
        parse_requirement("app[extra]"),
        parse_requirement("ignored; python_version < '0'"),
        parse_requirement("direct @ https://example.invalid/direct.whl"),
        parse_requirement("constrained"),
    )
    adapter = NabProvider(
        provider,
        ResolutionConfig(
            constraints=("constrained @ https://example.invalid/constrained.whl",),
        ),
    )
    root, root_range = adapter.add_root(parse_requirement("app"))
    adapter.choose_version(root, root_range)
    monkeypatch.setattr(adapter, "_versions", lambda _package: (Version("1"),))

    adapter.get_dependencies(root, Version("1"))

    assert provider.prefetch_calls == [("dep", "other")]


def test_dependency_prefetch_respects_an_existing_direct_requirement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = FakeProvider()
    provider.candidates[("app", Version("1"))].dependencies = (
        parse_requirement("dep<2"),
    )
    adapter = NabProvider(provider, ResolutionConfig())
    adapter.requirements["dep"] = parse_requirement(
        "dep @ https://example.invalid/dep.whl"
    )
    root, root_range = adapter.add_root(parse_requirement("app"))
    adapter.choose_version(root, root_range)
    monkeypatch.setattr(adapter, "_versions", lambda _package: (Version("1"),))

    adapter.get_dependencies(root, Version("1"))

    assert provider.prefetch_calls == []


@pytest.mark.parametrize("direct_first", [False, True])
def test_root_prefetch_gives_direct_urls_precedence(direct_first: bool) -> None:
    provider = FakeProvider()
    adapter = NabProvider(provider, ResolutionConfig())
    named = parse_requirement("dep")
    direct = parse_requirement("dep @ https://example.invalid/dep.whl")
    duplicates = [direct, named] if direct_first else [named, direct]

    adapter.add_roots([parse_requirement("app"), *duplicates])

    assert provider.prefetch_calls[0] == ("app",)
