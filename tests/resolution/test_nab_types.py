from __future__ import annotations

import pytest

import cpip.resolution.nab_types
from cpip._vendor.nab_resolver.ranges import Range
from cpip.core.packaging import SpecifierSet
from cpip.core.versions import Version


@pytest.mark.parametrize(
    "specifier, expected, expected_intersections",
    [
        ("!=1", Range.full(), 0),
        (">=1", Range.at_least(Version("1")), 0),
        ("<2", Range.less_than(Version("2")), 0),
        (
            ">1,<=2",
            Range.greater_than(Version("1")) & Range.at_most(Version("2")),
            1,
        ),
        (">=2,<1", Range.empty(), 1),
        ("==1", Range.singleton(Version("1")), 1),
    ],
)
def test_implied_range_skips_identity_intersections(
    monkeypatch: pytest.MonkeyPatch,
    specifier: str,
    expected: Range[Version],
    expected_intersections: int,
) -> None:
    intersections = 0
    original_and = Range.__and__

    def counting_and(self: Range[Version], other: object) -> Range[Version]:
        nonlocal intersections
        intersections += 1
        return original_and(self, other)

    monkeypatch.setattr(Range, "__and__", counting_and)

    assert cpip.resolution.nab_types._implied_range(SpecifierSet(specifier)) == expected
    assert intersections == expected_intersections
