"""Test specific for the --no-color option"""

import os
import shutil
import subprocess
import sys

import pytest
from kpip_test_support import KpipTestEnvironment


@pytest.mark.network
@pytest.mark.skipif(shutil.which("script") is None, reason="no 'script' executable")
def test_no_color(script: KpipTestEnvironment) -> None:
    """Ensure colour output disabled when --no-color is passed."""
    kpip_command = "kpip download {} setuptools==62.0.0 --no-cache-dir -d /tmp/"
    if sys.platform == "darwin":
        command = f"script -q /tmp/kpip-test-no-color.txt {kpip_command}"
    else:
        command = f'script -q /tmp/kpip-test-no-color.txt --command "{kpip_command}"'

    def get_run_output(option: str = "") -> str:
        cmd = command.format(option)
        proc = subprocess.Popen(
            cmd,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        proc.communicate()

        try:
            with open("/tmp/kpip-test-no-color.txt") as output_file:
                retval = output_file.read()
            return retval
        finally:
            os.unlink("/tmp/kpip-test-no-color.txt")
            os.unlink("/tmp/setuptools-62.0.0-py3-none-any.whl")

    assert "\x1b[3" in get_run_output(""), "Expected color in output"
    assert "\x1b[3" not in get_run_output("--no-color"), "Expected no color in output"
