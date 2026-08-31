"""The lazy wheel's suffix-range probe.

``LazyZipOverHTTP`` used to open with a HEAD (for length and Accept-Ranges)
followed by ranged GETs. One suffix range request now does both jobs: a 206
proves range support and delivers the tail holding the central directory, so
a typical wheel's metadata costs one request instead of two-plus, and a host
that serves ranges without advertising Accept-Ranges works. These tests run a
fake range-capable server over an in-memory zip and count requests.
"""

from __future__ import annotations

import io
import zipfile
from zipfile import ZipFile

import pytest
from cpip.network.lazy_wheel import (
    CONTENT_CHUNK_SIZE,
    HTTPRangeRequestUnsupported,
    LazyZipOverHTTP,
)


class FakeResponse:
    def __init__(self, status: int, headers: dict[str, str], body: bytes) -> None:
        self.status = status
        self.headers = headers
        self.reason = "" if status < 400 else "error"
        self.url = "https://example.invalid/demo.whl"
        self.body_internal = io.BytesIO(body)

    def read(self, amt: int = -1) -> bytes:
        return self.body_internal.read(amt)

    def drain_conn(self) -> None:
        self.body_internal.read()

    def close(self) -> None:
        pass


class RangeServerSession:
    """A range-capable HTTP server over one in-memory file.

    Deliberately has no ``head`` method: the probe must not issue one.
    """

    def __init__(
        self,
        payload: bytes,
        *,
        ranges: bool = True,
        suffix_416: bool = False,
    ) -> None:
        self.payload = payload
        self.ranges = ranges
        self.suffix_416 = suffix_416
        self.requests: list[str | None] = []

    def get(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        stream: bool = False,
    ) -> FakeResponse:
        length = len(self.payload)
        range_header = (headers or {}).get("Range")
        self.requests.append(range_header)

        if range_header is None or not self.ranges:
            return FakeResponse(
                200,
                {"Content-Length": str(length)},
                self.payload,
            )

        spec = range_header.removeprefix("bytes=")
        start_text, _, end_text = spec.partition("-")

        if not start_text:
            suffix = int(end_text)
            if suffix > length and self.suffix_416:
                return FakeResponse(
                    416,
                    {"Content-Range": f"bytes */{length}"},
                    b"",
                )
            start = max(0, length - suffix)
            end = length - 1
        else:
            start = int(start_text)
            end = min(int(end_text), length - 1)

        return FakeResponse(
            206,
            {"Content-Range": f"bytes {start}-{end}/{length}"},
            self.payload[start : end + 1],
        )


def zip_payload(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def test_small_wheel_costs_one_request() -> None:
    payload = zip_payload({"demo-1.0.dist-info/METADATA": b"Name: demo\n"})
    session = RangeServerSession(payload)

    with LazyZipOverHTTP("https://example.invalid/demo.whl", session) as lazy:  # type: ignore[arg-type]
        with ZipFile(lazy) as archive:
            metadata = archive.read("demo-1.0.dist-info/METADATA")

    assert metadata == b"Name: demo\n"
    assert session.requests == [f"bytes=-{CONTENT_CHUNK_SIZE}"]


def test_large_archive_fetches_the_rest_by_absolute_ranges() -> None:
    import random

    big = random.Random(0).randbytes(4 * CONTENT_CHUNK_SIZE)
    payload = zip_payload(
        {"pad.bin": big, "demo-1.0.dist-info/METADATA": b"Name: demo\n"},
    )
    assert len(payload) > CONTENT_CHUNK_SIZE
    session = RangeServerSession(payload)

    with LazyZipOverHTTP("https://example.invalid/demo.whl", session) as lazy:  # type: ignore[arg-type]
        with ZipFile(lazy) as archive:
            metadata = archive.read("demo-1.0.dist-info/METADATA")
            padding = archive.read("pad.bin")

    assert metadata == b"Name: demo\n"
    assert padding == big
    assert session.requests[0] == f"bytes=-{CONTENT_CHUNK_SIZE}"
    assert all(request is not None for request in session.requests)


def test_host_ignoring_range_reports_unsupported() -> None:
    payload = zip_payload({"demo-1.0.dist-info/METADATA": b"Name: demo\n"})
    session = RangeServerSession(payload, ranges=False)

    with pytest.raises(HTTPRangeRequestUnsupported):
        LazyZipOverHTTP("https://example.invalid/demo.whl", session)  # type: ignore[arg-type]


def test_tiny_file_416_falls_back_to_one_full_get() -> None:
    payload = zip_payload({"demo-1.0.dist-info/METADATA": b"Name: demo\n"})
    assert len(payload) < CONTENT_CHUNK_SIZE
    session = RangeServerSession(payload, suffix_416=True)

    with LazyZipOverHTTP("https://example.invalid/demo.whl", session) as lazy:  # type: ignore[arg-type]
        with ZipFile(lazy) as archive:
            metadata = archive.read("demo-1.0.dist-info/METADATA")

    assert metadata == b"Name: demo\n"
    assert session.requests == [f"bytes=-{CONTENT_CHUNK_SIZE}", None]
