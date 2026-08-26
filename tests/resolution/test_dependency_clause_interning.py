"""Dependency clauses replayed after a backjump should reuse the formula."""

from __future__ import annotations

from typing import Any

from cpip._vendor.nab_resolver import decide, incompat_index
from cpip._vendor.nab_resolver.ranges import Range
from cpip._vendor.nab_resolver.resolver import Resolver
from cpip._vendor.nab_resolver.types import Incompatibility, IncompatibilityCause


class Provider:
    def receive_partial_solution_hint(self, ranges: Any, decisions: Any) -> None:
        return None

    def choose_version(self, package: str, version_range: Range) -> int | None:
        return 2

    def consume_pending_clauses(self) -> list[Any]:
        return []

    def consume_force_backtrack_targets(self) -> list[Any]:
        return []

    def get_dependencies(self, package: str, version: int) -> dict[str, Range]:
        return {"dependency": Range.at_least(1)}

    def widen_decision(self, package: str, version: int) -> Range | None:
        return None

    def consume_dependency_invalidations(self) -> list[Any]:
        return []


def resolver() -> Resolver[str, int]:
    return Resolver(Provider())


def test_dependency_clause_is_reused_when_parent_range_is_covered() -> None:
    candidate = resolver()
    dependency_range = Range.at_least(1)
    broad = Range.between(1, 4)

    first = incompat_index.add_dependency_incompatibility(
        candidate, "parent", broad, "dependency", dependency_range
    )
    repeated = incompat_index.add_dependency_incompatibility(
        candidate, "parent", Range.singleton(2), "dependency", dependency_range
    )

    assert repeated is first
    assert candidate.incompatibilities == [first]
    assert candidate.package_to_incompatibilities == {
        "parent": [0],
        "dependency": [0],
    }
    assert (
        candidate.dependency_index[("parent", "dependency", dependency_range, False)]
        == 0
    )


def test_dependency_clause_widens_in_place() -> None:
    candidate = resolver()
    dependency_range = Range.at_least(1)
    first = incompat_index.add_dependency_incompatibility(
        candidate, "parent", Range.singleton(1), "dependency", dependency_range
    )
    widened = incompat_index.add_dependency_incompatibility(
        candidate, "parent", Range.singleton(3), "dependency", dependency_range
    )

    assert widened is not first
    assert candidate.incompatibilities == [widened]
    assert widened.terms[0].constraint == Range.singleton(1) | Range.singleton(3)
    assert candidate.package_to_incompatibilities["parent"] == [0]
    assert candidate.package_to_incompatibilities["dependency"] == [0]


def test_distinct_dependency_keys_get_distinct_clauses() -> None:
    candidate = resolver()
    incompat_index.add_dependency_incompatibility(
        candidate, "parent", Range.singleton(1), "dependency", Range.at_least(1)
    )
    incompat_index.add_dependency_incompatibility(
        candidate, "parent", Range.singleton(1), "dependency", Range.at_least(2)
    )
    incompat_index.add_dependency_incompatibility(
        candidate, "parent", Range.singleton(1), "other", Range.at_least(1)
    )

    assert len(candidate.incompatibilities) == 3
    assert len(candidate.dependency_index) == 3


def test_replayed_clause_still_replays_requirement_refinement(monkeypatch: Any) -> None:
    """Interning must not skip refinements that a backtrack removed."""
    candidate = resolver()
    cause: Incompatibility[Any, Any] = Incompatibility(
        [], IncompatibilityCause.DEPENDENCY
    )
    candidate.solution.derive("parent", Range.full(), positive=True, cause=cause)
    absorbed: list[Incompatibility[Any, Any]] = []

    def record_absorb(
        resolver: Any, package: Any, requirement: Any, clause: Any
    ) -> None:
        absorbed.append(clause)

    monkeypatch.setattr(decide, "absorb_redundant_requirement", record_absorb)

    candidate._decide_next("parent")
    candidate.solution.backtrack(0)
    candidate._decide_next("parent")

    assert len(candidate.incompatibilities) == 1
    assert absorbed == [candidate.incompatibilities[0]] * 2


def test_leaf_skips_widening_but_still_consumes_invalidations(
    monkeypatch: Any,
) -> None:
    candidate = resolver()
    cause: Incompatibility[Any, Any] = Incompatibility(
        [], IncompatibilityCause.DEPENDENCY
    )
    candidate.solution.derive("leaf", Range.full(), positive=True, cause=cause)
    consumed = 0

    monkeypatch.setattr(candidate.provider, "get_dependencies", lambda *_args: {})

    def reject_widen(*_args: Any) -> None:
        raise AssertionError("leaf decisions must not be widened")

    def consume() -> list[Any]:
        nonlocal consumed
        consumed += 1
        return []

    monkeypatch.setattr(candidate.provider, "widen_decision", reject_widen)
    monkeypatch.setattr(candidate.provider, "consume_dependency_invalidations", consume)

    candidate._decide_next("leaf")

    assert consumed == 1
