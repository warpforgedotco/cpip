from __future__ import annotations

import pytest

import cpip.resolution.api
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
