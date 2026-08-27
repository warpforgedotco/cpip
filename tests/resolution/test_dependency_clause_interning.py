"""Dependency clauses replayed after a backjump should reuse the formula."""

from __future__ import annotations

from typing import Any

import pytest
from cpip._vendor.nab_resolver import decide, incompat_index, propagate
from cpip._vendor.nab_resolver.ranges import Range
from cpip._vendor.nab_resolver.resolver import Resolver, Solution
from cpip._vendor.nab_resolver.types import (
    Incompatibility,
    IncompatibilityCause,
    RootRequirement,
    Term,
)


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


def test_solution_is_explicitly_unhashable() -> None:
    solution = Solution(pins={"package": 1}, edges=(), roots=("package",))

    with pytest.raises(TypeError):
        hash(solution)


def test_replacement_value_types_reject_attribute_deletion() -> None:
    solution = Solution(pins={"package": 1}, edges=(), roots=("package",))
    requirement = RootRequirement("package", Range.singleton(1))

    with pytest.raises(AttributeError, match="cannot delete field 'pins'"):
        del solution.pins
    with pytest.raises(AttributeError, match="cannot delete field 'constraint'"):
        del requirement.constraint


def test_range_token_memo_is_bounded() -> None:
    candidate = resolver()

    for version in range(propagate.RANGE_ID_MEMO_MAX + 1):
        propagate._intern_range(candidate, Range.singleton(version))  # noqa: SLF001

    assert len(candidate.range_tokens) == 1
    assert candidate.next_range_token == propagate.RANGE_ID_MEMO_MAX + 1


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
    assert candidate.package_to_incompatibilities == {"dependency": [0]}
    assert candidate.dependency_parent_incompatibilities == {"parent": [0]}
    assert candidate.dependency_parent_fallbacks == {"parent": [0]}
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
    assert candidate.package_to_incompatibilities["dependency"] == [0]
    assert candidate.dependency_parent_incompatibilities["parent"] == [0]
    assert candidate.dependency_parent_fallbacks["parent"] == [0]


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


def test_exact_parent_decision_dispatches_only_its_version_clauses() -> None:
    candidate = resolver()
    first = incompat_index.add_dependency_incompatibility(
        candidate,
        "parent",
        Range.singleton(1),
        "dependency",
        Range.singleton(1),
        exact_parent_version=1,
    )
    second = incompat_index.add_dependency_incompatibility(
        candidate,
        "parent",
        Range.singleton(2),
        "dependency",
        Range.singleton(2),
        exact_parent_version=2,
    )
    general = Incompatibility(
        [Term("parent", Range.singleton(3), positive=True)],
        IncompatibilityCause.NO_VERSIONS,
    )
    incompat_index.add_incompatibility(candidate, general)
    candidate.solution.decide("parent", 2)

    groups = propagate._related_incompatibility_groups(candidate, "parent")

    assert groups == ([2], (), [1])
    assert propagate._related_incompatibility_groups(candidate, "dependency") == (
        [0, 1],
        (),
    )
    assert candidate.incompatibilities == [first, second, general]


def test_reused_dependency_clause_registers_each_exact_parent_version() -> None:
    candidate = resolver()
    dependency_range = Range.singleton(1)
    first = incompat_index.add_dependency_incompatibility(
        candidate,
        "parent",
        Range.between(1, 4),
        "dependency",
        dependency_range,
        exact_parent_version=1,
    )
    repeated = incompat_index.add_dependency_incompatibility(
        candidate,
        "parent",
        Range.singleton(2),
        "dependency",
        dependency_range,
        exact_parent_version=2,
    )

    candidate.solution.decide("parent", 2)

    assert repeated is first
    assert candidate.dependency_parent_versions["parent"] == {1: [0], 2: [0]}
    assert propagate._related_incompatibility_groups(candidate, "parent") == (
        (),
        (),
        [0],
    )


def test_undecided_parent_dispatches_every_dependency_clause() -> None:
    candidate = resolver()
    cause: Incompatibility[Any, Any] = Incompatibility(
        [], IncompatibilityCause.DEPENDENCY
    )
    for version in (1, 2):
        incompat_index.add_dependency_incompatibility(
            candidate,
            "parent",
            Range.singleton(version),
            "dependency",
            Range.singleton(version),
            exact_parent_version=version,
        )
    candidate.solution.derive("parent", Range.singleton(2), positive=True, cause=cause)

    assert propagate._related_incompatibility_groups(candidate, "parent") == (
        (),
        [0, 1],
    )


def test_unit_propagation_reuses_the_classified_assignment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = resolver()
    excluded = Range.singleton(2)
    cause: Incompatibility[Any, Any] = Incompatibility(
        [], IncompatibilityCause.DEPENDENCY
    )
    candidate.solution.derive("gate", Range.full(), positive=True, cause=cause)
    candidate.solution.derive("gate", excluded, positive=False, cause=cause)
    incompatibility = Incompatibility(
        [
            Term("target", excluded, positive=True),
            Term("gate", excluded, positive=False),
        ],
        IncompatibilityCause.DEPENDENCY,
    )
    incompat_index.add_incompatibility(candidate, incompatibility)

    get_calls: list[str] = []
    solution_get = candidate.solution.get

    def counting_get(package: str) -> Range | None:
        get_calls.append(package)
        return solution_get(package)

    monkeypatch.setattr(candidate.solution, "get", counting_get)

    assert propagate.unit_propagation(candidate, "gate") is None
    assert solution_get("target") == ~excluded
    assert candidate.stats.derivations == 1
    assert get_calls == ["target", "gate", "target", "target", "target"]


def test_widened_merge_promotes_exact_clause_to_fallback() -> None:
    candidate = resolver()
    dependency_range = Range.at_least(1)
    incompat_index.add_dependency_incompatibility(
        candidate,
        "parent",
        Range.singleton(1),
        "dependency",
        dependency_range,
        exact_parent_version=1,
    )
    incompat_index.add_dependency_incompatibility(
        candidate,
        "parent",
        Range.between(1, 4),
        "dependency",
        dependency_range,
    )
    candidate.solution.decide("parent", 3)

    assert propagate._related_incompatibility_groups(candidate, "parent") == (
        (),
        [0],
        (),
    )


def test_unhashable_parent_version_uses_exhaustive_fallback() -> None:
    class Version:
        __hash__ = None

        def __init__(self, value: int) -> None:
            self.value = value

        def __eq__(self, other: object) -> bool:
            return isinstance(other, Version) and self.value == other.value

        def __lt__(self, other: Version) -> bool:
            return self.value < other.value

    candidate = resolver()
    version = Version(1)
    incompat_index.add_dependency_incompatibility(
        candidate,
        "parent",
        Range.singleton(version),
        "dependency",
        Range.singleton(1),
        exact_parent_version=version,
    )
    candidate.solution.decide("parent", version)

    assert propagate._related_incompatibility_groups(candidate, "parent") == (
        (),
        [0],
    )


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
