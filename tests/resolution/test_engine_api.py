from __future__ import annotations

from types import SimpleNamespace

import pytest

import cpip.resolution.api
from cpip.core.packaging import parse_requirement
from cpip.core.versions import Version
from cpip.resolution.api import ResolutionConfig, ResolutionEngine

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

    monkeypatch.setattr(cpip.resolution.api, "installed_index", snapshot)
    engine = ResolutionEngine(
        provider=FakeProvider(),
    )

    result = engine.resolve(["app"])

    assert len(result.candidates) == 2
    assert snapshot_calls == 1


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
