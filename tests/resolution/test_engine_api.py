from __future__ import annotations

from types import SimpleNamespace

import pytest

import kpip.resolution.api
from kpip.core.errors import ResolutionError
from kpip.core.packaging import parse_requirement
from kpip.core.versions import Version
from kpip.resolution.api import ResolutionConfig, ResolutionEngine
from kpip.resolution.nab_provider import NabProvider

from .test_nab_provider import FakeProvider


def test_resolution_config_is_immutable() -> None:
    config = ResolutionConfig(find_links=("/wheels",), constraints=("demo<2",))

    assert config.find_links == ("/wheels",)
    assert config.constraints == ("demo<2",)


def test_resolution_snapshots_installed_state_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot_calls = 0

    def snapshot() -> dict:
        nonlocal snapshot_calls
        snapshot_calls += 1
        return {}

    monkeypatch.setattr(kpip.resolution.api, "installed_index", snapshot)
    engine = ResolutionEngine(
        provider=FakeProvider(),
    )

    result = engine.resolve(["app"])

    assert len(result.candidates) == 2
    assert snapshot_calls == 1


def test_resolution_reuses_the_solvers_dependency_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kpip._vendor.nab_resolver.resolver import Resolver

    dependency_calls = 0
    calls_when_solved = None
    get_dependencies = NabProvider.get_dependencies
    solve = Resolver.solve

    def counting_get_dependencies(adapter, package, version):
        nonlocal dependency_calls
        dependency_calls += 1
        return get_dependencies(adapter, package, version)

    def solve_and_snapshot_calls(resolver, requirements, constraints=None):
        nonlocal calls_when_solved
        solution = solve(resolver, requirements, constraints)
        calls_when_solved = dependency_calls
        return solution

    monkeypatch.setattr(NabProvider, "get_dependencies", counting_get_dependencies)
    monkeypatch.setattr(Resolver, "solve", solve_and_snapshot_calls)

    result = ResolutionEngine(
        provider=FakeProvider(),
        ignore_installed=True,
    ).resolve(["app"])

    assert dependency_calls == calls_when_solved
    assert result.graph == {
        "app": frozenset({"dep"}),
        "dep": frozenset(),
    }


def test_late_dependency_extras_revisit_an_existing_decision() -> None:
    class LateExtrasProvider(FakeProvider):
        def __init__(self) -> None:
            super().__init__()
            self.versions = {
                name: (Version("1"),)
                for name in ("app", "aaa-feature", "zzz-consumer", "addon")
            }
            dependencies = {
                "app": (
                    parse_requirement("aaa-feature"),
                    parse_requirement("zzz-consumer"),
                ),
                "aaa-feature": (parse_requirement("addon; extra == 'enabled'"),),
                "zzz-consumer": (parse_requirement("aaa-feature[enabled]"),),
                "addon": (),
            }
            self.candidates = {
                (name, Version("1")): SimpleNamespace(
                    name=name,
                    canonical_name=name,
                    version=Version("1"),
                    dependencies=package_dependencies,
                    source_url=f"file:///{name}.whl",
                    source_kind="wheel",
                )
                for name, package_dependencies in dependencies.items()
            }

    result = ResolutionEngine(
        provider=LateExtrasProvider(),
        ignore_installed=True,
    ).resolve(["app"])

    assert {candidate.canonical_name for candidate in result.candidates} == {
        "aaa-feature",
        "addon",
        "app",
        "zzz-consumer",
    }
    assert result.graph == {
        "aaa-feature": frozenset({"addon"}),
        "addon": frozenset(),
        "app": frozenset({"aaa-feature", "zzz-consumer"}),
        "zzz-consumer": frozenset({"aaa-feature"}),
    }


def test_root_dependency_extras_revisit_an_existing_root_decision() -> None:
    class RootExtrasProvider(FakeProvider):
        def __init__(self) -> None:
            super().__init__()
            self.versions = {
                name: (Version("1"),)
                for name in ("aaa-feature", "zzz-consumer", "addon")
            }
            dependencies = {
                "aaa-feature": (parse_requirement("addon; extra == 'enabled'"),),
                "zzz-consumer": (parse_requirement("aaa-feature[enabled]"),),
                "addon": (),
            }
            self.candidates = {
                (name, Version("1")): SimpleNamespace(
                    name=name,
                    canonical_name=name,
                    version=Version("1"),
                    dependencies=package_dependencies,
                    source_url=f"file:///{name}.whl",
                    source_kind="wheel",
                )
                for name, package_dependencies in dependencies.items()
            }

    result = ResolutionEngine(
        provider=RootExtrasProvider(),
        ignore_installed=True,
    ).resolve(["aaa-feature", "zzz-consumer"])

    assert {candidate.canonical_name for candidate in result.candidates} == {
        "aaa-feature",
        "addon",
        "zzz-consumer",
    }


def test_unselected_root_candidate_does_not_expand_another_roots_extras() -> None:
    class ConstrainedRootProvider(FakeProvider):
        def __init__(self) -> None:
            super().__init__()
            self.versions = {
                "source": (Version("1"), Version("2")),
                "feature": (Version("1"),),
                "addon": (Version("1"),),
            }
            dependencies = {
                ("source", Version("2")): (parse_requirement("feature[enabled]"),),
                ("source", Version("1")): (),
                ("feature", Version("1")): (
                    parse_requirement("addon; extra == 'enabled'"),
                ),
                ("addon", Version("1")): (),
            }
            self.candidates = {
                (name, version): SimpleNamespace(
                    name=name,
                    canonical_name=name,
                    version=version,
                    dependencies=package_dependencies,
                    source_url=f"file:///{name}-{version}.whl",
                    source_kind="wheel",
                )
                for (name, version), package_dependencies in dependencies.items()
            }

    result = ResolutionEngine(
        provider=ConstrainedRootProvider(),
        constraints=["source==1"],
        ignore_installed=True,
    ).resolve(["source", "feature"])

    assert {candidate.canonical_name for candidate in result.candidates} == {
        "feature",
        "source",
    }


def test_self_dependency_extras_rebuild_selected_dependencies() -> None:
    class SelfExtrasProvider(FakeProvider):
        def __init__(self) -> None:
            super().__init__()
            self.versions = {
                name: (Version("1"),) for name in ("self-feature", "addon")
            }
            dependencies = {
                "self-feature": (
                    parse_requirement("self-feature[first]"),
                    parse_requirement("self-feature[second]; extra == 'first'"),
                    parse_requirement("addon; extra == 'second'"),
                ),
                "addon": (),
            }
            self.candidates = {
                (name, Version("1")): SimpleNamespace(
                    name=name,
                    canonical_name=name,
                    version=Version("1"),
                    dependencies=package_dependencies,
                    source_url=f"file:///{name}.whl",
                    source_kind="wheel",
                )
                for name, package_dependencies in dependencies.items()
            }

    result = ResolutionEngine(
        provider=SelfExtrasProvider(),
        ignore_installed=True,
    ).resolve(["self-feature"])

    assert {candidate.canonical_name for candidate in result.candidates} == {
        "addon",
        "self-feature",
    }
    assert result.graph == {
        "addon": frozenset(),
        "self-feature": frozenset({"addon", "self-feature"}),
    }


def test_self_dependency_rejects_an_incompatible_selected_version() -> None:
    provider = FakeProvider()
    provider.versions = {"app": (Version("1"),)}
    provider.candidates = {
        ("app", Version("1")): SimpleNamespace(
            name="app",
            canonical_name="app",
            version=Version("1"),
            dependencies=(parse_requirement("app==2"),),
            source_url="file:///app.whl",
            source_kind="wheel",
        ),
    }

    with pytest.raises(ResolutionError):
        ResolutionEngine(provider=provider, ignore_installed=True).resolve(["app"])


def test_rejected_self_dependency_does_not_leak_its_extras() -> None:
    provider = FakeProvider()
    provider.versions = {
        "app": (Version("2"), Version("3")),
        "addon": (Version("1"),),
    }
    provider.candidates = {
        ("app", Version("3")): SimpleNamespace(
            name="app",
            canonical_name="app",
            version=Version("3"),
            dependencies=(parse_requirement("app[enabled]==2"),),
            source_url="file:///app-3.whl",
            source_kind="wheel",
        ),
        ("app", Version("2")): SimpleNamespace(
            name="app",
            canonical_name="app",
            version=Version("2"),
            dependencies=(parse_requirement("addon; extra == 'enabled'"),),
            source_url="file:///app-2.whl",
            source_kind="wheel",
        ),
        ("addon", Version("1")): SimpleNamespace(
            name="addon",
            canonical_name="addon",
            version=Version("1"),
            dependencies=(),
            source_url="file:///addon.whl",
            source_kind="wheel",
        ),
    }

    result = ResolutionEngine(provider=provider, ignore_installed=True).resolve(["app"])

    assert {
        (candidate.canonical_name, candidate.version) for candidate in result.candidates
    } == {("app", Version("2"))}
    assert result.graph == {"app": frozenset()}


def test_root_dependency_extras_rebuild_an_installed_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class InstalledExtrasProvider(FakeProvider):
        def __init__(self) -> None:
            super().__init__()
            self.versions = {
                "aaa-feature": (),
                "zzz-consumer": (Version("1"),),
                "addon": (Version("1"),),
            }
            self.candidates = {
                ("zzz-consumer", Version("1")): SimpleNamespace(
                    name="zzz-consumer",
                    canonical_name="zzz-consumer",
                    version=Version("1"),
                    dependencies=(parse_requirement("aaa-feature[enabled]"),),
                    source_url="file:///zzz-consumer.whl",
                    source_kind="wheel",
                ),
                ("addon", Version("1")): SimpleNamespace(
                    name="addon",
                    canonical_name="addon",
                    version=Version("1"),
                    dependencies=(),
                    source_url="file:///addon.whl",
                    source_kind="wheel",
                ),
            }

    def installed_dependencies(extras=frozenset()):
        return [parse_requirement("addon")] if "enabled" in extras else []

    installed = SimpleNamespace(
        name="aaa-feature",
        canonical_name="aaa-feature",
        version=Version("1"),
        location="/installed/aaa-feature",
        dependencies=installed_dependencies,
    )
    monkeypatch.setattr(
        kpip.resolution.api,
        "installed_index",
        lambda: {"aaa-feature": installed},
    )

    result = ResolutionEngine(
        provider=InstalledExtrasProvider(),
        ignore_installed=False,
    ).resolve(["aaa-feature", "zzz-consumer"])

    assert {candidate.canonical_name for candidate in result.candidates} == {
        "addon",
        "zzz-consumer",
    }
    assert {resolved.requirement.name for resolved in result.satisfied} == {
        "aaa-feature"
    }
