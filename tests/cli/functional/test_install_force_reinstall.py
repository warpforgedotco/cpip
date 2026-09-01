import os

from kpip_test_support import KpipTestEnvironment, assert_all_changes


def check_installed_version(
    script: KpipTestEnvironment,
    package: str,
    expected: str,
) -> None:
    result = script.kpip("show", package)
    lines = result.stdout.splitlines()
    version = None
    for line in lines:
        if line.startswith("Version: "):
            version = line.split()[-1]
            break
    assert version == expected, f"version {version} != {expected}"


def check_force_reinstall(
    script: KpipTestEnvironment,
    specifier: str,
    expected: str,
) -> None:
    """Args:
    specifier: the requirement specifier to force-reinstall.
    expected: the expected version after force-reinstalling.

    """
    result = script.kpip_install_local("simplewheel==1.0")
    check_installed_version(script, "simplewheel", "1.0")

    to_fix = script.site_packages_path.joinpath("simplewheel", "__init__.py")
    to_fix.unlink()

    result2 = script.kpip_install_local("--force-reinstall", specifier)
    check_installed_version(script, "simplewheel", expected)

    fixed_key = os.path.relpath(to_fix, script.base_path)
    result2.did_create(fixed_key, message="force-reinstall failed")

    result3 = script.kpip("uninstall", "simplewheel", "-y")
    assert_all_changes(result, result3, [script.venv / "build", "cache"])


def test_force_reinstall_with_no_version_specifier(script: KpipTestEnvironment) -> None:
    """Check --force-reinstall when there is no version specifier and the
    installed version is not the newest version.
    """
    check_force_reinstall(script, "simplewheel", "2.0")


def test_force_reinstall_with_same_version_specifier(
    script: KpipTestEnvironment,
) -> None:
    """Check --force-reinstall when the version specifier equals the installed
    version and the installed version is not the newest version.
    """
    check_force_reinstall(script, "simplewheel==1.0", "1.0")
