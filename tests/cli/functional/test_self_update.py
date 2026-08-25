from pathlib import Path
from typing import Any


def test_self_update_editable(script: Any, cpip_src: Any, common_wheels: Path) -> None:
    script.cpip("install", "--no-index", "-f", common_wheels, "flit-core")

    proc = script.cpip("install", "--no-build-isolation", "--no-deps", cpip_src)
    assert proc.returncode == 0
    proc = script.cpip("install", "--no-build-isolation", "--no-deps", "-e", cpip_src)
    assert proc.returncode == 0
