from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_vendored_requests_ignores_host_detection_modules(tmp_path: Path) -> None:
    tmp_path.joinpath("chardet.py").write_text(
        "raise RuntimeError('host chardet was imported')\n",
        encoding="utf-8",
    )
    tmp_path.joinpath("simplejson.py").write_text(
        "raise RuntimeError('host simplejson was imported')\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    source_root = str(Path(__file__).parents[2] / "src")
    environment["PYTHONPATH"] = os.pathsep.join((str(tmp_path), source_root))

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from cpip._vendor.requests import compat; "
            "print(compat.chardet.__name__, compat.json.__name__)",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.stdout.strip() == "cpip._vendor.charset_normalizer json"
