from __future__ import annotations

from pathlib import Path

from cpip_benchmark.record import default_output, preflight_failures


def test_quiet_machine_on_mains_records() -> None:
    assert (
        preflight_failures(load=0.4, max_load=2.0, battery=False, throttled=False) == []
    )


def test_load_above_the_ceiling_refuses() -> None:
    failures = preflight_failures(
        load=20.35, max_load=2.0, battery=False, throttled=False
    )

    assert len(failures) == 1
    assert "20.35" in failures[0]
    assert "2.00" in failures[0]


def test_load_exactly_at_the_ceiling_is_allowed() -> None:
    assert (
        preflight_failures(load=2.0, max_load=2.0, battery=False, throttled=False) == []
    )


def test_battery_refuses_even_when_idle() -> None:
    # An idle machine on battery still won't hold P-core boost for a sweep
    # long enough to matter, so the numbers drift under their own recording.
    failures = preflight_failures(load=0.1, max_load=2.0, battery=True, throttled=False)

    assert len(failures) == 1
    assert "battery" in failures[0]


def test_every_reason_is_reported_not_just_the_first() -> None:
    # Fixing one blocker and re-running only to hit the next one wastes a
    # quiet window, so all of them are named up front.
    failures = preflight_failures(load=20.0, max_load=2.0, battery=True, throttled=True)

    assert len(failures) == 3


def test_default_output_is_branch_and_timestamp_under_the_repo(tmp_path: Path) -> None:
    output = default_output(tmp_path)

    assert output.parent == tmp_path / "benchmark-runs"
    # No slashes survive from a branch name like perf/optimize-core-pip-24.
    assert "/" not in output.name
