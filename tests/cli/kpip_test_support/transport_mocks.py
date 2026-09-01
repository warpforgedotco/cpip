"""Small transport fakes for network tests."""

from __future__ import annotations

from io import BytesIO
from typing import Any

from kpip._vendor.urllib3._collections import HTTPHeaderDict
from kpip._vendor.urllib3.response import HTTPResponse


def make_response(
    *,
    status: int,
    reason: str,
    url: str,
    headers: dict[str, str],
    body: bytes,
    stream: bool = False,
) -> HTTPResponse:
    """Build a native response for tests without opening a socket."""
    source = BytesIO(body) if stream or not body else body
    return HTTPResponse(
        body=source,
        headers=headers,
        status=status,
        reason=reason,
        preload_content=not stream,
        decode_content=False,
        request_url=url,
    )


class FakeStream:
    def __init__(self, contents: bytes) -> None:
        self.io_internal = BytesIO(contents)

    def read(self, size: int = -1, **_: Any) -> bytes:
        return self.io_internal.read(size)

    def close(self) -> None:
        self.io_internal.close()

    @property
    def closed(self) -> bool:
        return self.io_internal.closed


class MockResponse(HTTPResponse):
    def __init__(self, contents: bytes) -> None:
        headers = HTTPHeaderDict()
        super().__init__(
            body=FakeStream(contents),
            headers=headers,
            status=200,
            reason="OK",
            preload_content=False,
            decode_content=False,
            request_url="",
        )


class BrokenStream(FakeStream):
    """A truncated stream used to exercise resume handling."""

    def __init__(self, contents: bytes) -> None:
        super().__init__(contents)
        self.read_started = False

    def read(self, size: int = -1, **_: Any) -> bytes:
        if not self.read_started:
            self.read_started = True
            return super().read(size)

        raise OSError("Connection broken: IncompleteRead")
