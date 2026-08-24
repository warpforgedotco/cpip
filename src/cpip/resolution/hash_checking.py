"""The guarantees ``--require-hashes`` is supposed to make.

Hash-checking mode is not only "verify a digest if one is supplied". It is a
mode in which nothing may be installed that could not have been named exactly
in advance, so that the set of bytes an install can produce is fixed before it
runs. Four rules follow, and all four have to hold or the mode means nothing:

* nothing from version control, whose contents are not addressed by a digest
  at all;
* nothing from a local directory, for the same reason;
* every requirement pinned with ``==``, since an unpinned requirement lets a
  later upload change what "the" artifact is;
* every requirement carrying a hash, since a pin alone does not say which
  bytes that release consists of.

The mode turns itself on as soon as any requirement carries a hash, which is
why the errors say so: a user who never typed ``--require-hashes`` can still
land here by adding one ``--hash`` line.

All failures are collected before any is raised. A requirements file that
breaks the rules usually breaks them on several lines, and reporting them one
run at a time turns one fix into five.
"""

from __future__ import annotations

from cpip.core.errors import (
    DirectoryUrlHashUnsupported,
    HashError,
    HashMissing,
    HashUnpinned,
    VcsHashUnsupported,
)

TYPE_CHECKING = False

if TYPE_CHECKING:
    from collections.abc import Container, Iterable

    from cpip.resolution.req_install import InstallRequirement


class HashErrors(HashError):
    """Every hash-mode failure in one report, grouped by kind.

    Each failure keeps its own exception type, so a caller can still ask what
    went wrong; the grouping only decides how they read together. Kinds are
    ordered by :attr:`HashError.order` -- hardest to act on first.
    """

    def __init__(self) -> None:
        super().__init__()
        self.errors: list[tuple[HashError, str]] = []

    def append(self, error: HashError, subject: str) -> None:
        self.errors.append((error, subject))

    def __bool__(self) -> bool:
        return bool(self.errors)

    def __str__(self) -> str:
        lines: list[str] = []
        previous: type[HashError] | None = None
        for error, subject in sorted(
            self.errors,
            key=lambda item: (item[0].order, item[1]),
        ):
            kind = type(error)
            if kind is not previous:
                lines.append(kind.head)
                previous = kind
            lines.append(f"    {subject}")
        return "\n".join(lines)


def describe(requirement: InstallRequirement) -> str:
    """How a requirement is named back to the user in an error."""
    text = str(requirement.req) if requirement.req is not None else ""
    if not text and requirement.link is not None:
        text = requirement.link.url
    source = getattr(requirement, "comes_from", None)
    if isinstance(source, str) and source:
        return f"{text} (from {source})"
    return text or "<unnamed requirement>"


def check_requirement(
    requirement: InstallRequirement,
    errors: HashErrors,
    pinned_by_constraint: Container[str] = (),
) -> None:
    """Record every rule ``requirement`` breaks.

    A requirement that cannot be hashed at all is reported only for that, and
    not also for the pin and digest it was never going to be able to carry.

    ``pinned_by_constraint`` names projects some constraint pins with ``==``.
    A bare ``base`` in requirements.txt against ``base==0.1.0`` in
    constraints.txt does resolve to one release, so the pin rule is satisfied
    even though the requirement itself carries no specifier.
    """
    link = requirement.link
    subject = describe(requirement)

    if link is not None and link.is_vcs:
        errors.append(VcsHashUnsupported(subject), subject)
        return

    if requirement.editable or (link is not None and link.is_existing_dir):
        errors.append(DirectoryUrlHashUnsupported(subject), subject)
        return

    # A direct URL already names one artifact, so there is nothing for a pin
    # to add; pip makes the same exemption.
    if (
        not requirement.is_direct
        and (requirement.req is None or not requirement.is_pinned)
        and (
            requirement.req is None
            or requirement.req.canonical_name not in pinned_by_constraint
        )
    ):
        errors.append(HashUnpinned(subject), subject)

    if not requirement.hashes(trust_internet=False):
        errors.append(HashMissing(subject), subject)


def constraint_pinned_names(constraints: Iterable[str]) -> set[str]:
    """Canonical names some constraint pins to a single release."""
    from cpip.core.packaging import parse_requirement

    pinned: set[str] = set()
    for raw in constraints:
        try:
            requirement = parse_requirement(raw)
        except ValueError:
            continue
        if requirement.specifier.is_pinned:
            pinned.add(requirement.canonical_name)
    return pinned


def enforce_hash_checking(
    requirements: Iterable[InstallRequirement],
    *,
    constraints: Iterable[str] = (),
) -> None:
    """Raise unless every requirement satisfies hash-checking mode.

    Call this once the full requirement set is known and before anything is
    fetched: the point of the mode is that an install which cannot be verified
    never starts, not that it is torn down halfway.
    """
    pinned_by_constraint = constraint_pinned_names(constraints)
    errors = HashErrors()
    for requirement in requirements:
        check_requirement(requirement, errors, pinned_by_constraint)
    if errors:
        raise errors
