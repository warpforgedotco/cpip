from os.path import exists

import pytest
from kpip_test_support import KpipTestEnvironment, TestData


@pytest.mark.network
@pytest.mark.xfail(reason="The --build option was removed")
def test_no_clean_option_blocks_cleaning_after_install(
    script: KpipTestEnvironment,
    data: TestData,
) -> None:
    """Test --no-clean option blocks cleaning after install"""
    build = script.base_path / "kpip-build"
    script.kpip(
        "install",
        "--no-clean",
        "--no-index",
        "--build",
        build,
        f"--find-links={data.find_links}",
        "simple",
        expect_temp=True,
        allow_stderr_warning=True,
    )
    assert exists(build)
