"""The targeted-backtrack queue and its membership set stay in lock-step.

The ordered queue got an O(1) membership set beside it; a drain that clears
one but not the other quietly disables targeted backtracking -- a consumed
culprit would read as still pending and never re-queue on a later threshold
crossing. Every queue path is exercised: the two enqueue sites, the drain in
``apply_targeted_backtrack``, its cap branch, the restart clear, and
``_reset``.
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


def queue_is_consistent(resolver: Resolver[str, int]) -> bool:
    return set(resolver.pending_targeted_backtrack) == set(
        resolver.pending_targeted_backtrack_set,
    )


def test_apply_drain_lets_a_culprit_requeue() -> None:
    resolver: Resolver[str, int] = Resolver(Provider())
    resolver.pending_targeted_backtrack.append("culprit")
    resolver.pending_targeted_backtrack_set.add("culprit")

    conflict.apply_targeted_backtrack(resolver)

    assert queue_is_consistent(resolver)
    assert conflict.force_targeted_backtrack(resolver, ["culprit"]) is None
    assert resolver.pending_targeted_backtrack == []
    assert queue_is_consistent(resolver)


def test_the_cap_branch_drains_both_collections() -> None:
    resolver: Resolver[str, int] = Resolver(Provider())
    resolver.stats.targeted_backtracks = resolver.MAX_TARGETED_BACKTRACKS
    resolver.pending_targeted_backtrack.append("culprit")
    resolver.pending_targeted_backtrack_set.add("culprit")

    assert conflict.apply_targeted_backtrack(resolver) is None

    assert resolver.pending_targeted_backtrack == []
    assert queue_is_consistent(resolver)


def test_restart_and_reset_drain_both_collections() -> None:
    resolver: Resolver[str, int] = Resolver(Provider())
    resolver.pending_targeted_backtrack.append("culprit")
    resolver.pending_targeted_backtrack_set.add("culprit")
    resolver.max_conflict_count = 99

    _, _, restarted = conflict.maybe_restart(resolver, 1, 1)

    assert restarted
    assert resolver.pending_targeted_backtrack == []
    assert queue_is_consistent(resolver)

    resolver.pending_targeted_backtrack.append("culprit")
    resolver.pending_targeted_backtrack_set.add("culprit")
    resolver._reset(None)  # noqa: SLF001

    assert resolver.pending_targeted_backtrack == []
    assert queue_is_consistent(resolver)


def test_force_targeted_backtrack_enqueues_through_the_set() -> None:
    resolver: Resolver[str, int] = Resolver(Provider())

    conflict.force_targeted_backtrack(resolver, ["a", "a", "b"])

    # Duplicates collapse; the drain leaves both collections empty.
    assert resolver.pending_targeted_backtrack == []
    assert queue_is_consistent(resolver)
