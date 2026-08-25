from __future__ import annotations

from types import SimpleNamespace

import pytest

import cpip.resolution.nab_provider
from cpip.core.packaging import parse_requirement
from cpip.core.versions import Version
from cpip.index.provider import CandidateProvider
from cpip.resolution.models import ResolutionConfig
from cpip.resolution.nab_provider import NabProvider


class FakeProvider:
    def __init__(self) -> None:
        self.available_calls = 0
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

    def find_candidates(self, requirement, *, allowed_versions):
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


def test_selected_release_materializes_without_catalog_rescan() -> None:
    provider = IndexedFakeProvider()
    adapter = NabProvider(provider, ResolutionConfig())

    package, version_range = adapter.add_root(parse_requirement("app"))

    assert adapter.choose_version(package, version_range) == Version("1")
    assert provider.release_calls == 1


def test_constraints_apply_to_transitive_dependencies() -> None:
    adapter = NabProvider(FakeProvider(), ResolutionConfig(constraints=("dep==2",)))
    root, root_range = adapter.add_root(parse_requirement("app"))

    assert root == "app"
    assert adapter.choose_version(root, root_range) == Version("1")
    dependencies = adapter.get_dependencies(root, Version("1"))

    assert not adapter.has_satisfying_version("dep", dependencies["dep"])


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
