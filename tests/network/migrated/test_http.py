from __future__ import annotations

import gzip
import io
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from cpip.network.http import HttpRequest, HttpResponse, NetworkSession


def test_session_decodes_gzip_responses() -> None:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            body = gzip.compress(b"compressed")
            self.send_response(200)
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        session = NetworkSession()
        url = f"http://127.0.0.1:{server.server_port}/catalog"
        response = session.get(url)
        assert response.content == b"compressed"
        assert response.headers.get("Content-Encoding") is None
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_session_reuses_direct_http_connection() -> None:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        connections = 0
        requests = 0

        def setup(self) -> None:
            super().setup()
            type(self).connections += 1

        def do_GET(self) -> None:
            type(self).requests += 1
            body = b"ok"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        session = NetworkSession()
        url = f"http://127.0.0.1:{server.server_port}/catalog"
        assert session.get(url).content == b"ok"

        responses: list[bytes] = []

        def request() -> None:
            responses.append(session.get(url).content)

        worker = threading.Thread(target=request)
        worker.start()
        worker.join()
        assert responses == [b"ok"]
        assert Handler.requests == 2
        assert Handler.connections == 1
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_session_coalesces_concurrent_gets() -> None:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        requests = 0

        def do_GET(self) -> None:
            type(self).requests += 1
            time.sleep(0.1)
            body = b"ok"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        session = NetworkSession()
        url = f"http://127.0.0.1:{server.server_port}/catalog"
        barrier = threading.Barrier(2)
        responses: list[bytes] = []

        def request() -> None:
            barrier.wait()
            responses.append(session.get(url).content)

        workers = [threading.Thread(target=request) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()

        assert responses == [b"ok", b"ok"]
        assert Handler.requests == 1
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_session_revalidates_stale_cache_with_conditional_headers(
    tmp_path,
) -> None:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        conditional_headers: dict[str, str | None] = {}
        full_responses = 0
        requests = 0

        def do_GET(self) -> None:
            type(self).requests += 1
            if self.headers.get("If-None-Match") or self.headers.get(
                "If-Modified-Since",
            ):
                type(self).conditional_headers = {
                    "If-None-Match": self.headers.get("If-None-Match"),
                    "If-Modified-Since": self.headers.get("If-Modified-Since"),
                }
                self.send_response(304)
                self.send_header("ETag", '"tag-2"')
                self.send_header("Cache-Control", "max-age=3600")
                self.end_headers()
                return
            type(self).full_responses += 1
            body = b"cached body"
            self.send_response(200)
            self.send_header("ETag", '"tag-1"')
            self.send_header("Last-Modified", "Mon, 01 Jan 2024 00:00:00 GMT")
            self.send_header("Cache-Control", "max-age=0")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        session = NetworkSession(cache=str(tmp_path / "http-cache"))
        url = f"http://127.0.0.1:{server.server_port}/catalog"

        first = session.get(url)
        assert first.content == b"cached body"
        assert not first.from_cache

        second = session.get(url)
        assert second.content == b"cached body"
        assert second.from_cache
        assert second.headers.get("ETag") == '"tag-2"'
        assert Handler.full_responses == 1
        assert Handler.conditional_headers == {
            "If-None-Match": '"tag-1"',
            "If-Modified-Since": "Mon, 01 Jan 2024 00:00:00 GMT",
        }

        third = session.get(url)
        assert third.content == b"cached body"
        assert third.from_cache
        assert Handler.requests == 2

        stored = json.loads(session.cache.get(url).decode("utf-8"))
        assert stored["etag"] == '"tag-2"'
        assert stored["headers"]["Cache-Control"] == "max-age=3600"
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_unchanged_immediately_stale_304_skips_metadata_rewrite(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = NetworkSession(cache=str(tmp_path / "http-cache"))
    assert session.cache is not None
    url = "https://example.invalid/simple/demo/"
    request = HttpRequest("GET", url, {})
    body = b"cached body"
    session.cache_response(
        HttpResponse(
            status_code=200,
            reason="OK",
            url=url,
            headers={"Cache-Control": "max-age=0", "ETag": '"tag"'},
            raw=io.BytesIO(body),
            content_internal=body,
            request=request,
        ),
    )
    raw_metadata = session.cache.get(url)
    assert raw_metadata is not None
    metadata = json.loads(raw_metadata)
    writes: list[tuple[str, bytes]] = []
    monkeypatch.setattr(session.cache, "set", lambda *args: writes.append(args))

    response = session.revalidated_response(
        request,
        metadata,
        {"Cache-Control": "max-age=0", "ETag": '"tag"'},
    )

    assert response.content == body
    assert writes == []


def test_environ_proxies_cached_per_host_and_port(
    monkeypatch,
) -> None:
    """NO_PROXY can name a port; the bypass decision must not be shared
    between two ports of the same host."""
    import urllib.parse

    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:3128")
    monkeypatch.setenv("NO_PROXY", "internal.test:8080")
    session = NetworkSession()

    bypassed_url = "http://internal.test:8080/simple/"
    proxied_url = "http://internal.test:9090/simple/"

    bypassed = session.environ_proxies_for(
        bypassed_url,
        urllib.parse.urlsplit(bypassed_url),
    )
    proxied = session.environ_proxies_for(
        proxied_url,
        urllib.parse.urlsplit(proxied_url),
    )

    assert bypassed == {}
    assert proxied.get("http") == "http://proxy.invalid:3128"


def test_redirect_gains_environment_proxy_for_destination(
    monkeypatch,
) -> None:
    """trust_env is off, so the redirect hook must add the destination's
    environment proxy itself (and still honor NO_PROXY for it)."""

    from cpip._vendor import requests

    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:3128")
    monkeypatch.setenv("NO_PROXY", "start.test")
    session = NetworkSession()
    session.ensure_requests_backend()
    transport = session.requests_session

    proxied = requests.Request("GET", "http://elsewhere.test/file").prepare()
    assert transport.rebuild_proxies(proxied, {}) == {
        "http": "http://proxy.invalid:3128",
    }

    bypassed = requests.Request("GET", "http://start.test/file").prepare()
    assert transport.rebuild_proxies(bypassed, {}) == {}


def test_redirect_reapplies_netrc_for_destination(tmp_path, monkeypatch) -> None:
    """A cross-host redirect strips Authorization; the destination must get
    its own netrc credentials, as requests did with trust_env on."""
    import base64

    netrc_path = tmp_path / "netrc"
    netrc_path.write_text("machine localhost login redirected password secret\n")
    monkeypatch.setenv("NETRC", str(netrc_path))

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        seen_authorization: list[str | None] = []

        def do_GET(self) -> None:
            if self.path == "/start":
                target = f"http://localhost:{self.server.server_port}/target"
                self.send_response(302)
                self.send_header("Location", target)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            type(self).seen_authorization.append(self.headers.get("Authorization"))
            body = b"ok"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        session = NetworkSession()
        url = f"http://127.0.0.1:{server.server_port}/start"
        response = session.get(url, headers={"Authorization": "Basic original"})
        assert response.content == b"ok"

        token = base64.b64encode(b"redirected:secret").decode()
        assert Handler.seen_authorization == [f"Basic {token}"]
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
