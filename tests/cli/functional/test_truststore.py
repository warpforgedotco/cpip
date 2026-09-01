from collections.abc import Callable
from typing import Any

import pytest
from kpip_test_support import KpipTestEnvironment, TestKpipResult

KpipRunner = Callable[..., TestKpipResult]


@pytest.fixture
def kpip_no_truststore(script: KpipTestEnvironment) -> KpipRunner:
    def kpip(*args: str, **kwargs: Any) -> TestKpipResult:
        return script.kpip(*args, "--use-deprecated=legacy-certs", **kwargs)

    return kpip


@pytest.mark.network
@pytest.mark.parametrize(
    "package",
    [
        "INITools",
        "https://github.com/pypa/pip-test-package/archive/refs/heads/master.zip",
    ],
    ids=["PyPI", "GitHub"],
)
def test_no_truststore_can_install(
    script: KpipTestEnvironment,
    kpip_no_truststore: KpipRunner,
    package: str,
) -> None:
    result = kpip_no_truststore("install", package)
    assert "Successfully installed" in result.stdout
