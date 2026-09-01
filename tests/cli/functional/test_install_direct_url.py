import pytest
from kpip_test_support import KpipTestEnvironment, TestData, create_test_package


def test_install_find_links_no_direct_url(script: KpipTestEnvironment) -> None:
    result = script.kpip_install_local("simple")
    assert not result.get_created_direct_url("simple")


def test_install_vcs_non_editable_direct_url(script: KpipTestEnvironment) -> None:
    pkg_path = create_test_package(script.scratch_path, name="testpkg")
    url = pkg_path.as_uri()
    args = ["install", "--no-build-isolation", f"git+{url}#egg=testpkg"]
    result = script.kpip(*args)
    direct_url = result.get_created_direct_url("testpkg")
    assert direct_url
    assert direct_url.url == url
    assert direct_url.vcs_info
    assert direct_url.vcs_info.vcs == "git"


def test_install_archive_direct_url(
    script: KpipTestEnvironment,
    data: TestData,
) -> None:
    req = "simple @ " + data.packages.joinpath("simple-2.0.tar.gz").as_uri()
    assert req.startswith("simple @ file://")
    result = script.kpip("install", "--no-build-isolation", req)
    assert result.get_created_direct_url("simple")


@pytest.mark.network
def test_install_vcs_constraint_direct_url(script: KpipTestEnvironment) -> None:
    constraints_file = script.scratch_path / "constraints.txt"
    constraints_file.write_text(
        "git+https://github.com/pypa/pip-test-package"
        "@5547fa909e83df8bd743d3978d6667497983a4b7"
        "#egg=pip-test-package",
    )
    result = script.kpip("install", "pip-test-package", "-c", constraints_file)
    assert result.get_created_direct_url("pip_test_package")


def test_install_vcs_constraint_direct_file_url(script: KpipTestEnvironment) -> None:
    pkg_path = create_test_package(script.scratch_path, name="testpkg")
    url = pkg_path.as_uri()
    constraints_file = script.scratch_path / "constraints.txt"
    constraints_file.write_text(f"git+{url}#egg=testpkg")
    result = script.kpip(
        "install",
        "--no-build-isolation",
        "testpkg",
        "-c",
        constraints_file,
    )
    assert result.get_created_direct_url("testpkg")
