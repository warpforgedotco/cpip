from pathlib import Path
from typing import Any


def test_self_update_editable(script: Any, kpip_src: Any, common_wheels: Path) -> None:
    script.kpip("install", "--no-index", "-f", common_wheels, "flit-core")

    proc = script.kpip("install", "--no-build-isolation", "--no-deps", kpip_src)
    assert proc.returncode == 0
    proc = script.kpip("install", "--no-build-isolation", "--no-deps", "-e", kpip_src)
    assert proc.returncode == 0
