import json
import os
from pathlib import Path
from venv import EnvBuilder

from kpip_test_support import KpipTestEnvironment, TestData


def test_python_interpreter(
    script: KpipTestEnvironment,
    tmpdir: Path,
    shared_data: TestData,
) -> None:
    env_path = os.fspath(tmpdir / "venv")
    env = EnvBuilder(with_pip=False)
    env.create(env_path)

    result = script.kpip("--python", env_path, "list", "--format=json")
    before = json.loads(result.stdout)

    script.kpip(
        "--python",
        env_path,
        "install",
        "-f",
        shared_data.find_links,
        "--no-index",
        "simplewheel==1.0",
    )

    result = script.kpip("--python", env_path, "list", "--format=json")
    installed = json.loads(result.stdout)
    assert {"name": "simplewheel", "version": "1.0"} in installed

    script.kpip("--python", env_path, "uninstall", "simplewheel", "--yes")
    result = script.kpip("--python", env_path, "list", "--format=json")
    assert json.loads(result.stdout) == before


def test_error_python_option_wrong_location(
    script: KpipTestEnvironment,
    tmpdir: Path,
    shared_data: TestData,
) -> None:
    env_path = os.fspath(tmpdir / "venv")
    env = EnvBuilder(with_pip=False)
    env.create(env_path)

    script.kpip("list", "--python", env_path, "--format=json", expect_error=True)
