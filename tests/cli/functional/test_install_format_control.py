"""Tests for --only-binary and --no-binary format control flags.

These tests verify edge case CLI and requirements file interaction behavior,
matching the pattern established by --all-releases and --only-final tests.
"""

from __future__ import annotations

from kpip_test_support import (
    KpipTestEnvironment,
    create_basic_sdist_for_package,
    create_basic_wheel_for_package,
)


def test_order_no_binary_then_only_binary(script: KpipTestEnvironment) -> None:
    """Test --no-binary=:all: --only-binary=<package>.

    When the user specifies --no-binary=:all: --only-binary=simple, they
    expect 'simple' to allow wheels (later flag overrides).
    """
    wheel_path = create_basic_wheel_for_package(script, "simple", "1.0")

    result = script.kpip_install_local(
        "--no-binary=:all:",
        "--only-binary=simple",
        "simple==1.0",
        find_links=[wheel_path.parent],
    )
    script.assert_installed(simple="1.0")
    assert "Building wheel for simple" not in result.stdout


def test_order_only_binary_then_no_binary(script: KpipTestEnvironment) -> None:
    """Test --only-binary=:all: --no-binary=<package>.

    When the user specifies --only-binary=:all: --no-binary=simple,
    'simple' should be built from source (later flag overrides).
    """
    wheel_path = create_basic_wheel_for_package(script, "simple", "1.0")
    create_basic_sdist_for_package(script, "simple", "1.0")

    result = script.kpip_install_local(
        "--only-binary=:all:",
        "--no-binary=simple",
        "simple==1.0",
        find_links=[wheel_path.parent],
    )
    script.assert_installed(simple="1.0")
    assert "Building wheel for simple" in result.stdout


def test_reqfile_no_binary_overrides_cmdline_only_binary(
    script: KpipTestEnvironment,
) -> None:
    """Test requirements file --no-binary overrides command line --only-binary."""
    wheel_path = create_basic_wheel_for_package(script, "simple", "1.0")
    create_basic_sdist_for_package(script, "simple", "1.0")

    req_file = script.temporary_file(
        "requirements.txt",
        f"--find-links {wheel_path.parent.as_posix()}\n"
        "--no-binary :all:\nsimple==1.0\n",
    )

    result = script.kpip_install_local(
        "--only-binary=:all:",
        "-r",
        req_file,
        find_links=[],
    )
    script.assert_installed(simple="1.0")
    assert "Building wheel for simple" in result.stdout


def test_reqfile_only_binary_overrides_cmdline_no_binary(
    script: KpipTestEnvironment,
) -> None:
    """Test requirements file --only-binary overrides command line --no-binary."""
    wheel_path = create_basic_wheel_for_package(script, "simple", "1.0")

    req_file = script.temporary_file(
        "requirements.txt",
        f"--find-links {wheel_path.parent.as_posix()}\n"
        "--only-binary :all:\nsimple==1.0\n",
    )

    result = script.kpip_install_local(
        "--no-binary=:all:",
        "-r",
        req_file,
        find_links=[],
    )
    result.assert_installed("simple", editable=False)
    assert "Building wheel for simple" not in result.stdout


def test_package_specific_overrides_all_in_requirements_file(
    script: KpipTestEnvironment,
) -> None:
    """Test package-specific setting overrides :all: in requirements file."""
    wheel_path = create_basic_wheel_for_package(script, "simple", "1.0")

    req_file = script.temporary_file(
        "requirements.txt",
        f"--find-links {wheel_path.parent.as_posix()}\n--no-binary :all:\n"
        "--only-binary simple\nsimple==1.0\n",
    )

    result = script.kpip_install_local("-r", req_file, find_links=[])
    result.assert_installed("simple", editable=False)
    assert "Building wheel for simple" not in result.stdout
