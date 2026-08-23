"""Benchmarks for the network session policy layer, offline.

``NetworkSession.request`` runs once per index page, metadata file, and
artifact, so its per-request overhead (header assembly, credential
resolution, HTTP-cache probing, coalescing bookkeeping) scales with every
resolve.  These benchmarks stub the transport with canned responses so they
measure only cpip's own policy code, never sockets.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from cpip.network.download import Downloader
from cpip.network.http import HttpRequest, HttpResponse, NetworkSession
from cpip.network.lazy_wheel import dist_from_wheel_url
from pytest_codspeed import BenchmarkFixture

from benchmark_support import make_wheel, simple_index_html

INDEX_URLS = [
    "https://mirror.invalid/simple/",
    "https://example.invalid/simple/",
]
PAGE_URLS = [f"https://example.invalid/simple/package-{index}/" for index in range(50)]
PAGE_BODY = simple_index_html(120).encode()
ARTIFACT_URLS = [
    f"https://example.invalid/packages/package-{index}-1.0.0-py3-none-any.whl"
    for index in range(8)
]
ARTIFACT_BODY = bytes(512 * 1024)


class FakeTransportSession(NetworkSession):
    """NetworkSession whose transport is a canned in-memory response table."""

    def __init__(
        self, bodies: dict[str, bytes], response_headers: dict[str, str], **kwargs
    ) -> None:
        super().__init__(**kwargs)
        self.bodies = bodies
        self.response_headers = response_headers

    def open_internal(
        self,
        request: HttpRequest,
        timeout,
        *,
        stream: bool = False,
    ) -> HttpResponse:
        body = self.bodies[request.url.partition("#")[0]]
        range_header = request.headers.get("Range")
        status = 200
        headers = dict(self.response_headers)
        if range_header:
            total = len(body)
            spec = range_header.partition("=")[2]
            start_text, _, end_text = spec.partition("-")
            start = int(start_text) if start_text else 0
            end = int(end_text) if end_text else total - 1
            body = body[start : end + 1]
            status = 206
            headers["Content-Range"] = f"bytes {start}-{end}/{total}"
        headers["Content-Length"] = str(len(body))
        return HttpResponse(
            status_code=status,
            reason="OK",
            url=request.url,
            headers=headers,
            raw=io.BytesIO(body),
            content_internal=None if stream else body,
            streaming=stream,
            request=request,
        )


def page_session(cache: str | None) -> FakeTransportSession:
    return FakeTransportSession(
        bodies=dict.fromkeys(PAGE_URLS, PAGE_BODY),
        response_headers={
            "Content-Type": "text/html",
            "Cache-Control": "max-age=86400",
            "ETag": '"deadbeef"',
        },
        index_urls=INDEX_URLS,
        cache=cache,
    )


@pytest.fixture(scope="session")
def warm_cache_dir(tmp_path_factory: pytest.TempPathFactory) -> str:
    """An HTTP cache primed with every index page, served fresh forever."""
    cache_dir = str(tmp_path_factory.mktemp("network-http-cache"))
    session = page_session(cache_dir)
    for url in PAGE_URLS:
        session.get(url).close()
    return cache_dir


def test_session_requests_cache_miss(benchmark: BenchmarkFixture) -> None:
    """Full request() policy overhead when every GET goes to the transport."""
    session = page_session(cache=None)

    def fetch_all() -> int:
        total = 0
        for url in PAGE_URLS:
            response = session.get(url)
            total += len(response.content)
            response.close()
        return total

    assert benchmark(fetch_all) == len(PAGE_BODY) * len(PAGE_URLS)


def test_session_requests_cache_hit(
    benchmark: BenchmarkFixture,
    warm_cache_dir: str,
) -> None:
    """request() served entirely from the on-disk HTTP cache."""
    session = page_session(warm_cache_dir)

    def fetch_all() -> int:
        total = 0
        for url in PAGE_URLS:
            response = session.get(url)
            assert response.from_cache
            total += len(response.content)
            response.close()
        return total

    assert benchmark(fetch_all) == len(PAGE_BODY) * len(PAGE_URLS)


def test_session_fresh_cache_probe(
    benchmark: BenchmarkFixture,
    warm_cache_dir: str,
) -> None:
    """has_fresh_cached_response() over a warm cache, memo cleared per pass."""
    session = page_session(warm_cache_dir)

    def probe_all() -> int:
        session.fresh_cached_response_cache.clear()
        return sum(session.has_fresh_cached_response(url) for url in PAGE_URLS)

    assert benchmark(probe_all) == len(PAGE_URLS)


def test_session_cache_store(
    benchmark: BenchmarkFixture,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """cache_response() writing metadata and body files for each response."""
    cache_dir = str(tmp_path_factory.mktemp("network-store-cache"))
    session = page_session(cache_dir)

    def store_all() -> int:
        count = 0
        for url in PAGE_URLS:
            response = session.open_internal(
                HttpRequest("GET", url, dict(session.headers)),
                timeout=None,
            )
            session.cache_response(response)
            count += 1
        return count

    assert benchmark(store_all) == len(PAGE_URLS)


def test_auth_credential_resolution(benchmark: BenchmarkFixture) -> None:
    """Per-request credential lookup against configured index URLs."""
    session = page_session(cache=None)
    auth = session.auth

    def resolve_all() -> int:
        count = 0
        for url in PAGE_URLS:
            resolved, username, password = auth.get_url_and_credentials(url)
            assert username is None
            assert password is None
            count += 1
        return count

    assert benchmark(resolve_all) == len(PAGE_URLS)


def test_download_artifacts(
    benchmark: BenchmarkFixture,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Streaming artifact downloads through the Downloader chunk loop."""
    from cpip.index.links import Link

    session = FakeTransportSession(
        bodies=dict.fromkeys(ARTIFACT_URLS, ARTIFACT_BODY),
        response_headers={"Content-Type": "application/octet-stream"},
    )
    downloader = Downloader(session)
    links = [Link.from_url(url, source_url=None) for url in ARTIFACT_URLS]
    location = str(tmp_path_factory.mktemp("network-downloads"))

    def download_all() -> int:
        return sum(1 for _ in downloader.batch(links, location))

    assert benchmark(download_all) == len(ARTIFACT_URLS)


@pytest.fixture(scope="session")
def range_wheel(tmp_path_factory: pytest.TempPathFactory) -> tuple[str, bytes]:
    wheelhouse = tmp_path_factory.mktemp("lazy-wheelhouse")
    path = make_wheel(wheelhouse, "lazy-target", "1.0.0", payload_files=50)
    url = f"https://example.invalid/packages/{path.name}"
    return url, Path(path).read_bytes()


def test_lazy_wheel_metadata(
    benchmark: BenchmarkFixture,
    range_wheel: tuple[str, bytes],
) -> None:
    """Range-request metadata extraction via LazyZipOverHTTP."""
    url, body = range_wheel
    session = FakeTransportSession(
        bodies={url: body},
        response_headers={
            "Content-Type": "application/octet-stream",
            "Accept-Ranges": "bytes",
        },
    )

    def read_metadata() -> str:
        dist = dist_from_wheel_url("lazy-target", url, session)
        return dist.metadata["Name"]

    assert benchmark(read_metadata) == "lazy-target"
