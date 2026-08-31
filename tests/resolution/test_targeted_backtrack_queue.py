"""The targeted-backtrack queue drains and re-fills correctly.

The queue is a dict-as-ordered-set: enqueue order stays deterministic while
membership is one key write. A drain that fails to empty it would quietly
disable targeted backtracking -- a consumed culprit reading as still pending
never re-queues on a later threshold crossing. Every queue path is
exercised, asserting the exact queue contents at drain time so a regression
that skips enqueueing cannot pass.
"""

from __future__ import annotations

from typing import Any

from cpip._vendor.nab_resolver import conflict
from cpip._vendor.nab_resolver.resolver import BaseProvider, Resolver


class Provider(BaseProvider[str, int]):
    def choose_version(self, package: str, version_range: Any) -> int:
        return 1

    def has_satisfying_version(self, package: str, version_range: Any) -> bool:
        return True

    def get_dependencies(self, package: str, version: int) -> dict[str, Any]:
        return {}

    def prioritize(self, package: str, *args: Any) -> int:
        return 0

    def widen_decision(self, package: str, version: int) -> None:
        return None


def drain_spy(
    monkeypatch: Any,
    drained: list[list[str]],
) -> None:
    """Record the queue at the moment ``force_targeted_backtrack`` drains it."""
    original = conflict.apply_targeted_backtrack

    def record(resolver: Resolver[str, int]) -> Any:
        drained.append(list(resolver.pending_targeted_backtrack))
        return original(resolver)

    monkeypatch.setattr(conflict, "apply_targeted_backtrack", record)


def test_apply_drain_lets_a_culprit_requeue(monkeypatch: Any) -> None:
    resolver: Resolver[str, int] = Resolver(Provider())
    resolver.pending_targeted_backtrack["culprit"] = None

    conflict.apply_targeted_backtrack(resolver)

    assert not resolver.pending_targeted_backtrack

    drained: list[list[str]] = []
    drain_spy(monkeypatch, drained)
    assert conflict.force_targeted_backtrack(resolver, ["culprit"]) is None

    # The consumed culprit really re-entered the queue before the drain.
    assert drained == [["culprit"]]
    assert not resolver.pending_targeted_backtrack


def test_the_cap_branch_drains_the_queue() -> None:
    resolver: Resolver[str, int] = Resolver(Provider())
    resolver.stats.targeted_backtracks = resolver.MAX_TARGETED_BACKTRACKS
    resolver.pending_targeted_backtrack["culprit"] = None

    assert conflict.apply_targeted_backtrack(resolver) is None

    assert not resolver.pending_targeted_backtrack


def test_restart_and_reset_drain_the_queue() -> None:
    resolver: Resolver[str, int] = Resolver(Provider())
    resolver.pending_targeted_backtrack["culprit"] = None
    resolver.max_conflict_count = 99

    _, _, restarted = conflict.maybe_restart(resolver, 1, 1)

    assert restarted
    assert not resolver.pending_targeted_backtrack

    resolver.pending_targeted_backtrack["culprit"] = None
    resolver._reset(None)  # noqa: SLF001

    assert not resolver.pending_targeted_backtrack


def test_force_targeted_backtrack_deduplicates_on_enqueue(
    monkeypatch: Any,
) -> None:
    resolver: Resolver[str, int] = Resolver(Provider())
    drained: list[list[str]] = []
    drain_spy(monkeypatch, drained)

    conflict.force_targeted_backtrack(resolver, ["a", "a", "b"])

    # Duplicates collapse on enqueue; the drain leaves the queue empty.
    assert drained == [["a", "b"]]
    assert not resolver.pending_targeted_backtrack
