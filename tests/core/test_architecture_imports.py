from __future__ import annotations

import ast
from pathlib import Path


ALLOWED_IMPORTS = {
    "core": frozenset(),
    "platform": frozenset({"core"}),
    "build": frozenset({"core", "platform"}),
    "index": frozenset({"core", "build", "platform"}),
    "network": frozenset({"core", "platform", "build", "index"}),
    "vcs": frozenset({"core"}),
    "resolution": frozenset({"core", "index", "network", "vcs"}),
    "install": frozenset(
        {"core", "platform", "build", "index", "network", "vcs", "resolution"},
    ),
    "cli": frozenset(
        {
            "core",
            "platform",
            "build",
            "index",
            "network",
            "vcs",
            "resolution",
            "install",
        },
    ),
}

KNOWN_DEBT = frozenset(
    {
        ("build/build_backend.py", "install"),
        ("resolution/inputs.py", "install"),
        ("resolution/api.py", "install"),
        ("resolution/req_install.py", "build"),
    },
)


def test_first_party_imports_follow_architecture() -> None:
    package_root = Path(__file__).parents[2] / "src" / "cpip"
    violations: list[str] = []
    for path in package_root.glob("*/*.py"):
        relative = path.relative_to(package_root)
        owner = relative.parts[0]
        if owner not in ALLOWED_IMPORTS:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(relative))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                modules = (node.module or "",)
            elif isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            else:
                continue
            for module in modules:
                if not module.startswith("cpip."):
                    continue
                target = module.split(".", 2)[1]
                if target in {owner, "_vendor"} or target in ALLOWED_IMPORTS[owner]:
                    continue
                debt_key = (relative.as_posix(), target)
                if debt_key not in KNOWN_DEBT:
                    violations.append(
                        f"{relative}:{node.lineno}: {owner} may not import {target}",
                    )

    assert not violations, "\n" + "\n".join(violations)
