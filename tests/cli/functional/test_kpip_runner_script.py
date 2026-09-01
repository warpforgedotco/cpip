import os
from pathlib import Path

from kpip import __version__
from kpip_test_support import KpipTestEnvironment


def test_runner_work_in_environments_with_no_pip(
    script: KpipTestEnvironment,
    kpip_src: Path,
) -> None:
    runner = kpip_src / "src" / "kpip" / "__kpip-runner__.py"

    script.kpip("uninstall", "kpip", "--yes", use_module=True)
    script.run("python", "-c", "import kpip", expect_error=True)

    result = script.run("python", os.fspath(runner), "--version")

    assert __version__ in result.stdout
