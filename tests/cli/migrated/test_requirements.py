from __future__ import annotations

import os
from pathlib import Path

from kpip.cli.requirements import collect_requirements
from kpip.core.appdirs import http_cache_path


def test_collect_requirements_uses_http_cache_directory(tmp_path: Path) -> None:
    bundle = collect_requirements(
        requirements=["demo"],
        cache_dir=os.fspath(tmp_path),
    )

    assert bundle.session.cache.directory == http_cache_path(os.fspath(tmp_path))
