import os
from pathlib import Path

from cpip import __version__
from cpip_test_support import CpipTestEnvironment


def test_runner_work_in_environments_with_no_pip(
    script: CpipTestEnvironment,
    cpip_src: Path,
) -> None:
    runner = cpip_src / "src" / "cpip" / "__cpip-runner__.py"

    script.cpip("uninstall", "cpip", "--yes", use_module=True)
    script.run("python", "-c", "import cpip", expect_error=True)

    result = script.run("python", os.fspath(runner), "--version")

    assert __version__ in result.stdout
