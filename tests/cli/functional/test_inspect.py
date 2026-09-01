import json

import pytest
from kpip_test_support import KpipTestEnvironment, ScriptFactory, TestData


@pytest.fixture
def simple_script(
    tmpdir_factory: pytest.TempPathFactory,
    script_factory: ScriptFactory,
    shared_data: TestData,
) -> KpipTestEnvironment:
    tmpdir = tmpdir_factory.mktemp("kpip_test_package")
    script = script_factory(tmpdir.joinpath("workspace"))
    script.kpip(
        "install",
        "-f",
        shared_data.find_links,
        "--no-index",
        "simplewheel==1.0",
    )
    return script


def test_inspect_basic(simple_script: KpipTestEnvironment) -> None:
    """Test default behavior of inspect command."""
    result = simple_script.kpip("inspect")
    report = json.loads(result.stdout)
    installed_by_name = {i["metadata"]["name"]: i for i in report["installed"]}
    installed_by_name.pop("coverage", None)
    assert len(installed_by_name) == 3
    assert installed_by_name.keys() == {
        "kpip",
        "setuptools",
        "simplewheel",
    }
    assert installed_by_name["simplewheel"]["metadata"]["version"] == "1.0"
    assert installed_by_name["simplewheel"]["requested"] is True
    assert installed_by_name["simplewheel"]["installer"] == "kpip"
    assert "environment" in report
