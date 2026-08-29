"""HTTP primitives used by cpip."""

from __future__ import annotations

import enum
import io
import json
import logging
import os
import sys
import threading
import time
import urllib.parse

from cpip._vendor.urllib3._collections import HTTPHeaderDict
from cpip._vendor.urllib3.exceptions import (
    MaxRetryError,
    NewConnectionError,
    ProtocolError,
    ProxyError,
    ReadTimeoutError,
    SSLError,
    TimeoutError,
)
from cpip._vendor.urllib3.response import HTTPResponse
from cpip._vendor.urllib3.util import Retry, Timeout, make_headers
from cpip.core.cpip_version import get_cpip_version
from cpip.core.urls import redact_auth_from_url, url_to_path
from cpip.core.utils import current_version
from cpip.network.auth import MultiDomainBasicAuth
from cpip.network.cache import SafeFileCache
from cpip.network.exceptions import (
    ConnectionFailedError,
    ConnectionTimeoutError,
    ProxyConnectionError,
    SSLVerificationError,
    TooManyRedirectsError,
)

TYPE_CHECKING = False

if TYPE_CHECKING:
    import email.message
    from collections.abc import Mapping, Sequence
    from typing import Any, NoReturn

    from cpip.core.http import HttpResponse as HttpResponseProtocol

logger = logging.getLogger(__name__)

RETRY_STATUS_CODES = frozenset((500, 502, 503, 520, 527))
DEFAULT_TIMEOUT = 15.0

_NOT_UPDATED_BY_304 = frozenset(
    (
        "content-length",
        "content-encoding",
        "content-range",
        "content-type",
        "transfer-encoding",
        "connection",
    ),
)


class _MissingCacheExpiry(enum.Enum):
    """Single-member enum so ``is not`` narrowing keeps the ``float | None`` type."""

    TOKEN = enum.auto()


_MISSING_CACHE_EXPIRY = _MissingCacheExpiry.TOKEN


class HttpRequest:
    __slots__ = ("body", "headers", "method", "url")

    def __init__(
        self,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
    ) -> None:
        self.method = method

        self.url = url

        self.headers = headers if headers is not None else {}

        self.body = body


class InFlightRequest:
    """State shared by callers waiting for one network request."""

    def __init__(self) -> None:
        self.event: threading.Event | None = None

        self.response: (
            tuple[
                int,
                str,
                str,
                Mapping[str, str] | email.message.Message,
                bytes,
            ]
            | None
        ) = None

        self.error: BaseException | None = None


class NetworkStats:
    """Optional counters for diagnosing network behavior."""

    __slots__ = (
        "artifact_requests",
        "cache_hits",
        "catalog_requests",
        "coalesced_waiters",
        "metadata_requests",
        "network_requests",
        "other_requests",
        "pypi_json_requests",
    )

    def __init__(self) -> None:
        self.cache_hits = 0

        self.coalesced_waiters = 0

        self.network_requests = 0

        self.catalog_requests = 0

        self.metadata_requests = 0

        self.pypi_json_requests = 0

        self.artifact_requests = 0

        self.other_requests = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "cache_hits": self.cache_hits,
            "coalesced_waiters": self.coalesced_waiters,
            "network_requests": self.network_requests,
            "catalog_requests": self.catalog_requests,
            "metadata_requests": self.metadata_requests,
            "pypi_json_requests": self.pypi_json_requests,
            "artifact_requests": self.artifact_requests,
            "other_requests": self.other_requests,
        }


def request_kind(url: str) -> str:
    path = urllib.parse.urlsplit(url).path.lower()

    if "/simple/" in path and path.endswith("/"):
        return "catalog"

    if path.endswith(".metadata"):
        return "metadata"

    if "/pypi/" in path and path.endswith("/json"):
        return "pypi_json"

    if path.endswith((".whl", ".tar.gz", ".zip", ".tar.bz2", ".tar.xz")):
        return "artifact"

    return "other"


class NetworkSession:
    """urllib3-backed HTTP(S) client with cpip-specific policy."""

    def __init__(
        self,
        *,
        retries: int = 0,
        resume_retries: int = 0,
        trusted_hosts: Sequence[str] = (),
        index_urls: list[str] | None = None,
        cache: Any = None,
    ) -> None:
        self.headers = make_headers(
            user_agent=self.user_agent(),
            accept_encoding="gzip",
        )

        self.proxies: dict[str, str] | None = None

        self.cpip_proxy: str | None = None

        self.cpip_no_proxy_env = False

        self.cpip_custom_cert: str | None = None

        self.cpip_client_cert: str | None = None

        self.verify: bool | str = True

        self.cert: str | None = None

        self.retry = Retry(
            total=None,
            connect=retries,
            read=retries,
            redirect=30,
            status=retries,
            other=0,
            allowed_methods=None,
            status_forcelist=RETRY_STATUS_CODES,
            backoff_factor=0.25,
            raise_on_status=False,
        )

        self.timeout = Timeout(connect=DEFAULT_TIMEOUT, read=DEFAULT_TIMEOUT)

        self.resume_retries = resume_retries

        self.auth: Any = MultiDomainBasicAuth(index_urls=index_urls)

        self.pool_managers: dict[tuple[Any, ...], Any] = {}

        self.transport_lock = threading.Lock()

        if isinstance(cache, str):
            self.cache = SafeFileCache(cache)

        else:
            self.cache = cache

        self.trusted_hosts = {host.lower().split(":", 1)[0] for host in trusted_hosts}

        self.inflight_requests: dict[tuple[Any, ...], InFlightRequest] = {}

        self.inflight_requests_lock = threading.Lock()

        # Hosts observed not to serve HTTP range requests. Probing one costs a
        # HEAD plus a failed read, so the answer is remembered for the process
        # rather than rediscovered per candidate. Only ever grows, and only
        # from a definite answer -- a timeout or a 500 says nothing about
        # whether ranges work.
        self.no_range_requests: set[str] = set()

        self.no_range_requests_lock = threading.Lock()

        self.fresh_cached_response_cache: dict[str, float | None] = {}

        self.environ_proxies_cache: dict[tuple[str, int | None], dict[str, str]] = {}

        self.environ_ca_bundle = os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get(
            "CURL_CA_BUNDLE",
        )

        self.network_stats = (
            NetworkStats()
            if os.environ.get("CPIP_BENCH_NETWORK_STATS") == "1"
            else None
        )

    def supports_range_requests(self, url: str) -> bool:
        """Whether this host has *not* been observed to refuse range requests."""
        host = urllib.parse.urlsplit(url).netloc.lower()

        with self.no_range_requests_lock:
            return host not in self.no_range_requests

    def wheel_metadata_text(self, url: str, name: str) -> str | None:
        """A remote wheel's ``METADATA``, read with HTTP range requests.

        Pulls the zip's central directory and the one member, rather than the
        whole wheel, so resolution can learn a candidate's dependencies
        without downloading it. Returns ``None`` when that is not possible --
        the host does not serve ranges, the response is not a usable zip, the
        wheel has no readable metadata -- and the caller falls back to
        downloading, which is what it did before this existed.

        A host that refuses ranges is remembered, so the probe is paid once
        per host per process instead of once per candidate.
        """
        host = urllib.parse.urlsplit(url).netloc.lower()

        with self.no_range_requests_lock:
            if host in self.no_range_requests:
                return None

        from cpip.network.lazy_wheel import (
            HTTPRangeRequestUnsupported,
            metadata_text_from_wheel_url,
        )

        try:
            return metadata_text_from_wheel_url(name, url, self)

        except HTTPRangeRequestUnsupported:
            with self.no_range_requests_lock:
                self.no_range_requests.add(host)

            return None

        except Exception:
            # Anything else says nothing about whether this host serves
            # ranges, so it is not remembered: a wheel that is not a zip, a
            # connection that dropped, an index that answered oddly once.
            return None

    @staticmethod
    def user_agent() -> str:
        version = current_version()

        if version is None:
            version = get_cpip_version()

        python_version = "%s.%s.%s" % sys.version_info[:3]

        return f"cpip/{version} Python/{python_version}"

    def get(self, url: str, **kwargs: Any) -> HttpResponseProtocol:
        return self.request("GET", url, **kwargs)

    def head(self, url: str, **kwargs: Any) -> HttpResponseProtocol:
        return self.request("HEAD", url, **kwargs)

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        data: bytes | None = None,
        stream: bool = False,
        timeout: Timeout | float | tuple[float | None, float | None] | None = None,
        _auth_retry: bool = True,
    ) -> HttpResponseProtocol:
        cached_metadata = None

        cached_body: bytes | None = None

        cacheable = method == "GET" and not stream
        if cacheable and headers is not None:
            cacheable = not any(str(name).lower() == "range" for name in headers)

        auth_resolver = getattr(self.auth, "get_url_and_credentials", None)

        # MultiDomainBasicAuth only removes credentials from the URL. When
        # there are none embedded in the authority, the cache key is already
        # final and a fresh hit does not need credential or netrc resolution.
        looked_up_before_auth = cacheable and (
            auth_resolver is None
            or (isinstance(self.auth, MultiDomainBasicAuth) and "@" not in url)
        )

        if looked_up_before_auth:
            cached, cached_metadata, cached_body = self.cache_lookup(url)

            if cached is not None:
                if self.network_stats is not None:
                    self.network_stats.cache_hits += 1

                return cached

        request_url = url

        username: str | None = None

        password: str | None = None

        if auth_resolver is not None:
            request_url, username, password = auth_resolver(url)

        if cacheable and not looked_up_before_auth:
            cached, cached_metadata, cached_body = self.cache_lookup(request_url)

            if cached is not None:
                if self.network_stats is not None:
                    self.network_stats.cache_hits += 1

                return cached

        request_headers = self.headers.copy()

        if headers is not None:
            for name, value in headers.items():
                request_headers[str(name).lower()] = str(value)

        if stream:
            request_headers["accept-encoding"] = "identity"

        if username is not None and password is not None:
            request_headers.update(
                make_headers(basic_auth=f"{username}:{password}"),
            )

        request = HttpRequest(
            method,
            request_url,
            request_headers,
            data,
        )

        if cached_metadata is not None:
            etag = cached_metadata.get("etag")

            if etag:
                request_headers["if-none-match"] = str(etag)

            last_modified = cached_metadata.get("last_modified")

            if last_modified:
                request_headers["if-modified-since"] = str(last_modified)

        request_timeout = timeout if timeout is not None else self.timeout

        try:
            response = self.open_coalesced(
                request,
                timeout=request_timeout,
                stream=stream,
            )

        except MaxRetryError as exc:
            self.raise_transport_error(
                exc.reason if isinstance(exc.reason, Exception) else exc,
                request_url,
                self.timeout_for_urllib3(request_timeout),
            )

        except (
            NewConnectionError,
            TimeoutError,
            SSLError,
            ProxyError,
            ProtocolError,
        ) as exc:
            self.raise_transport_error(
                exc,
                request_url,
                self.timeout_for_urllib3(request_timeout),
            )

        except OSError as exc:
            self.raise_transport_error(
                exc,
                request_url,
                self.timeout_for_urllib3(request_timeout),
            )

        if response.status == 401 and self.auth is not None and _auth_retry:
            retry = self.retry_auth(
                response,
                request,
                headers or {},
                data,
                timeout,
                stream=stream,
            )

            if retry is not None:
                return retry

        if response.status == 304 and cached_metadata is not None:
            response.close()

            return self.revalidated_response(
                request,
                cached_metadata,
                response.headers,
                cached_body,
            )

        if method == "GET" and not stream and response.status == 200:
            self.cache_response(response)

        return response

    def open_coalesced(
        self,
        request: HttpRequest,
        timeout: Any,
        *,
        stream: bool = False,
    ) -> HttpResponseProtocol:
        if request.method != "GET" or stream or "range" in request.headers:
            return self.open_internal(request, timeout, stream=stream)

        key = (request.url, frozenset(request.headers.items()))

        wait_event = None

        with self.inflight_requests_lock:
            flight = self.inflight_requests.get(key)

            if flight is None:
                flight = InFlightRequest()

                self.inflight_requests[key] = flight

                owner = True

            else:
                owner = False

                wait_event = flight.event

                if wait_event is None:
                    wait_event = threading.Event()

                    flight.event = wait_event

        if not owner:
            if self.network_stats is not None:
                self.network_stats.coalesced_waiters += 1

            assert wait_event is not None

            wait_event.wait()

            if flight.error is not None:
                raise flight.error

            if flight.response is None:
                raise ProtocolError(
                    f"coalesced request completed without a response: {request.url}",
                )

            status, reason, url, headers, body = flight.response

            response: Any = HTTPResponse(
                body=body or io.BytesIO(),
                headers=HTTPHeaderDict(headers.items()),
                status=status,
                reason=reason,
                decode_content=False,
                request_url=url,
            )
            return response

        response = None

        try:
            if self.network_stats is not None:
                self.network_stats.network_requests += 1

                kind = request_kind(request.url)

                setattr(
                    self.network_stats,
                    f"{kind}_requests",
                    getattr(self.network_stats, f"{kind}_requests") + 1,
                )

            response = self.open_internal(request, timeout, stream=stream)

            return response

        except BaseException as exc:
            flight.error = exc

            raise

        finally:
            with self.inflight_requests_lock:
                self.inflight_requests.pop(key, None)

                wait_event = flight.event

            try:
                if response is not None and wait_event is not None:
                    flight.response = (
                        response.status,
                        response.reason,
                        response.url,
                        response.headers,
                        response.data,
                    )

            except BaseException as exc:
                flight.error = exc

                raise

            finally:
                if wait_event is not None:
                    wait_event.set()

    def cache_lookup(
        self,
        url: str,
    ) -> tuple[
        HttpResponseProtocol | None,
        dict[str, Any] | None,
        bytes | None,
    ]:
        """One cache read: a fresh response, stale metadata for revalidation, or neither."""

        if self.cache is None:
            return None, None, None

        get_with_body = getattr(self.cache, "get_with_body", None)

        if get_with_body is not None:
            metadata, body = get_with_body(url)

        else:
            metadata = self.cache.get(url)

            body = self.cache.get_body(url)

        if metadata is None:
            if body is not None:
                body.close()

            return None, None, None

        try:
            values = json.loads(metadata.decode("utf-8"))

            expires_at = values.get("expires_at")

            if expires_at is not None and float(expires_at) <= time.time():
                if not values.get("etag") and not values.get("last_modified"):
                    if body is not None:
                        body.close()

                    return None, values, None

                if body is None:
                    return None, values, None

                try:
                    body_data = body.read()
                finally:
                    body.close()

                return None, values, body_data

            headers = values["headers"]

            status = int(values["status"])

            reason = str(values["reason"])

        except (AttributeError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            if body is not None:
                body.close()

            return None, None, None

        if body is None:
            return None, None, None

        try:
            body_data = body.read()
        finally:
            body.close()

        response: Any = HTTPResponse(
            body=body_data if body_data else io.BytesIO(),
            headers=HTTPHeaderDict(headers),
            status=status,
            reason=reason,
            decode_content=False,
            request_url=url,
        )
        response.from_cache = True
        return response, None, None

    def has_fresh_cached_response(self, url: str) -> bool:
        """Check cache freshness without reading the cached response body."""

        if self.cache is None or not hasattr(self.cache, "get_body_path"):
            return False

        cached_expiry = self.fresh_cached_response_cache.get(
            url,
            _MISSING_CACHE_EXPIRY,
        )

        if cached_expiry is not _MISSING_CACHE_EXPIRY:
            if cached_expiry is None or cached_expiry > time.time():
                return True

            self.fresh_cached_response_cache.pop(url, None)

        metadata = self.cache.get(url)

        if metadata is None:
            return False

        try:
            values = json.loads(metadata.decode("utf-8"))

            expires_at = values.get("expires_at")

            expires_at_value = None if expires_at is None else float(expires_at)

            if expires_at_value is not None and expires_at_value <= time.time():
                return False

        except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
            return False

        self.fresh_cached_response_cache[url] = expires_at_value

        return True

    def revalidated_response(
        self,
        request: HttpRequest,
        metadata: dict[str, Any],
        response_headers: Any = None,
        cached_body: bytes | None = None,
    ) -> HttpResponseProtocol:
        self.fresh_cached_response_cache.pop(request.url, None)

        headers = metadata.get("headers", {})

        if not isinstance(headers, dict):
            headers = {}

        original_headers = headers

        if response_headers is not None:
            headers = dict(headers)

            lowered = {name.lower(): name for name in headers}

            for name, value in response_headers.items():
                key = name.lower()

                if key in _NOT_UPDATED_BY_304:
                    continue

                existing = lowered.get(key)

                if existing is not None and existing != name:
                    del headers[existing]

                headers[name] = str(value)

                lowered[key] = name

        merged = HTTPHeaderDict(headers)

        expires_at = self.cache_expiry(merged)

        if expires_at is None:
            expires_at = time.time()

        etag = merged.get("ETag")

        last_modified = merged.get("Last-Modified")

        metadata_changed = (
            headers != original_headers
            or metadata.get("etag") != etag
            or metadata.get("last_modified") != last_modified
        )

        # An unchanged ``max-age=0`` response will be stale on the next read
        # regardless of whether its newly computed timestamp is persisted.
        # Avoid an atomic metadata rewrite (including fsync) in that common
        # revalidation loop. Positive freshness or changed validators/headers
        # still have to reach disk for the next process.
        if metadata_changed or expires_at > time.time():
            updated = dict(metadata)

            updated["headers"] = headers

            updated["expires_at"] = expires_at

            updated["etag"] = etag

            updated["last_modified"] = last_modified

            self.cache.set(request.url, json.dumps(updated).encode("utf-8"))

        if cached_body is None:
            body = self.cache.get_body(request.url)

            if body is None:
                raise ProtocolError(
                    f"Cached response body missing for url: {request.url}",
                )

            try:
                body_data = body.read()
            finally:
                body.close()

        else:
            body_data = cached_body

        response: Any = HTTPResponse(
            body=body_data if body_data else io.BytesIO(),
            headers=merged,
            status=int(metadata.get("status", 200)),
            reason=str(metadata.get("reason", "OK")),
            decode_content=False,
            request_url=request.url,
        )
        response.from_cache = True
        return response

    @staticmethod
    def cache_expiry(
        headers: Mapping[str, str] | email.message.Message | HTTPHeaderDict,
    ) -> float | None:
        import email.utils

        cache_control = headers.get("Cache-Control", "")

        for directive in cache_control.split(","):
            directive = directive.strip().lower()

            if directive.startswith("max-age="):
                try:
                    return time.time() + max(0, int(directive[8:]))

                except ValueError:
                    break

        expires = headers.get("Expires")

        if expires:
            try:
                return email.utils.parsedate_to_datetime(expires).timestamp()

            except (TypeError, ValueError, OverflowError):
                pass

        return None

    def cache_response(self, response: HttpResponseProtocol) -> None:
        if self.cache is None:
            return

        self.fresh_cached_response_cache.pop(response.url, None)

        cache_control = response.headers.get("Cache-Control", "")

        directives = {
            part.strip().lower() for part in cache_control.split(",") if part.strip()
        }

        if "no-store" in directives:
            return

        body = response.data

        expires_at = self.cache_expiry(response.headers)

        cached_headers = HTTPHeaderDict(response.headers)
        cached_headers.discard("Transfer-Encoding")
        cached_headers["Content-Length"] = str(len(body))
        if getattr(response, "_has_decoded_content", False):
            cached_headers.discard("Content-Encoding")

        metadata = json.dumps(
            {
                "status": response.status,
                "reason": response.reason,
                "url": response.url,
                "headers": dict(cached_headers.items()),
                "expires_at": expires_at,
                "etag": response.headers.get("ETag"),
                "last_modified": response.headers.get("Last-Modified"),
            },
        ).encode("utf-8")

        set_with_body = getattr(self.cache, "set_with_body", None)
        if set_with_body is not None:
            set_with_body(response.url, metadata, body)
        else:
            self.cache.set(response.url, metadata)
            self.cache.set_body(response.url, body)

    def open_internal(
        self,
        request: HttpRequest,
        timeout: Any,
        *,
        stream: bool = False,
    ) -> HttpResponseProtocol:
        if urllib.parse.urlsplit(request.url).scheme == "file":
            return self.open_file(request)

        transport_timeout = self.timeout_for_urllib3(
            timeout if timeout is not None else self.timeout,
        )

        return self.open_with_redirects(
            request,
            transport_timeout,
            stream=stream,
        )

    def environ_proxies_for(
        self,
        url: str,
        parsed: urllib.parse.SplitResult,
    ) -> dict[str, str]:
        """Resolve environment and system proxies once per host and port.

        NO_PROXY entries can name a port, so the bypass decision is keyed by
        both. The result is cached so urllib3 does not repeat the native
        system-configuration call made by ``urllib.request`` on macOS.
        """

        try:
            port = parsed.port

        except ValueError:
            port = None

        key = (parsed.hostname or "", port)

        cached = self.environ_proxies_cache.get(key)

        if cached is None:
            import urllib.request

            if urllib.request.proxy_bypass(parsed.netloc):
                cached = {}

            else:
                cached = {
                    str(name).lower(): str(value)
                    for name, value in urllib.request.getproxies().items()
                }

            self.environ_proxies_cache[key] = cached

        return cached

    def proxies_for(
        self,
        url: str,
        parsed: urllib.parse.SplitResult,
    ) -> dict[str, str]:
        proxies = self.environ_proxies_for(url, parsed)

        if self.proxies is not None:
            return {**proxies, **self.proxies} if proxies else dict(self.proxies)

        return proxies

    def proxy_for(
        self,
        url: str,
        parsed: urllib.parse.SplitResult | None = None,
    ) -> str | None:
        parsed = parsed or urllib.parse.urlsplit(url)

        proxies = self.proxies_for(url, parsed)

        host_key = f"{parsed.scheme}://{parsed.hostname}"

        all_host_key = f"all://{parsed.hostname}"

        return (
            proxies.get(host_key)
            or proxies.get(parsed.scheme)
            or proxies.get(all_host_key)
            or proxies.get("all")
        )

    def transport_manager(
        self,
        url: str,
        *,
        verify: bool | str,
    ) -> Any:
        from cpip._vendor import urllib3
        from cpip._vendor import certifi

        parsed = urllib.parse.urlsplit(url)

        proxy = self.proxy_for(url, parsed)

        cert = self.cert

        key = (proxy, verify, cert)

        with self.transport_lock:
            manager = self.pool_managers.get(key)

            if manager is not None:
                return manager

            kwargs: dict[str, Any] = {
                "num_pools": 64,
                "maxsize": 64,
            }

            if verify is False:
                kwargs["cert_reqs"] = "CERT_NONE"

            else:
                kwargs["cert_reqs"] = "CERT_REQUIRED"

                kwargs["ca_certs"] = (
                    verify
                    if isinstance(verify, str)
                    else self.environ_ca_bundle or certifi.where()
                )

            if cert is not None:
                if isinstance(cert, tuple):
                    kwargs["cert_file"], kwargs["key_file"] = cert

                else:
                    kwargs["cert_file"] = cert

            if proxy:
                proxy_url, proxy_headers = self.prepare_proxy(proxy)

                manager = urllib3.ProxyManager(
                    proxy_url,
                    proxy_headers=proxy_headers,
                    **kwargs,
                )

            else:
                manager = urllib3.PoolManager(**kwargs)

            self.pool_managers[key] = manager

            return manager

    @staticmethod
    def prepare_proxy(proxy: str) -> tuple[str, dict[str, str]]:
        if "://" not in proxy:
            proxy = f"http://{proxy}"

        parsed = urllib.parse.urlsplit(proxy)

        headers: dict[str, str] = {}

        if parsed.username is not None:
            username = urllib.parse.unquote(parsed.username)

            password = urllib.parse.unquote(parsed.password or "")

            headers.update(
                make_headers(proxy_basic_auth=f"{username}:{password}"),
            )

            hostname = parsed.hostname or ""

            if ":" in hostname and not hostname.startswith("["):
                hostname = f"[{hostname}]"

            netloc = hostname

            if parsed.port is not None:
                netloc += f":{parsed.port}"

            proxy = urllib.parse.urlunsplit(parsed._replace(netloc=netloc))

        return proxy, headers

    @staticmethod
    def timeout_for_urllib3(
        timeout: Timeout | float | tuple[float | None, float | None] | None,
    ) -> Timeout:
        if isinstance(timeout, Timeout):
            return timeout

        if isinstance(timeout, tuple):
            return Timeout(connect=timeout[0], read=timeout[1])

        return Timeout.from_float(timeout)

    def raise_transport_error(
        self,
        error: Exception,
        url: str,
        timeout: Timeout,
    ) -> NoReturn:
        redacted_url = redact_auth_from_url(url)

        host = urllib.parse.urlsplit(url).hostname or "unknown host"

        if isinstance(error, NewConnectionError):
            raise ConnectionFailedError(redacted_url, host, error) from error

        if isinstance(error, TimeoutError):
            kind = "read" if isinstance(error, ReadTimeoutError) else "connect"

            value = timeout.read_timeout if kind == "read" else timeout.connect_timeout

            raise ConnectionTimeoutError(
                redacted_url,
                host,
                kind=kind,
                timeout=float(value) if isinstance(value, (int, float)) else 0,
            ) from error

        if isinstance(error, SSLError):
            raise SSLVerificationError(redacted_url, host, error) from error

        if isinstance(error, ProxyError):
            proxy = self.proxy_for(url) or "configured proxy"

            raise ProxyConnectionError(redacted_url, proxy, error) from error

        raise ConnectionFailedError(redacted_url, host, error) from error

    def open_with_redirects(
        self,
        request: HttpRequest,
        timeout: Timeout,
        *,
        stream: bool,
    ) -> HttpResponseProtocol:
        current_method = request.method

        current_url = request.url

        current_headers: dict[str, str] | HTTPHeaderDict = request.headers

        current_body = request.body

        retries = self.retry

        while True:
            parsed = urllib.parse.urlsplit(current_url)
            verify: bool | str = self.verify
            if parsed.hostname and parsed.hostname.lower() in self.trusted_hosts:
                verify = False
            elif verify is True and self.environ_ca_bundle is not None:
                verify = self.environ_ca_bundle

            manager = self.transport_manager(current_url, verify=verify)

            raw = manager.request(
                current_method,
                current_url,
                body=current_body,
                headers=current_headers,
                redirect=False,
                retries=retries,
                timeout=timeout,
                preload_content=not stream,
                decode_content=True,
            )

            # Connection pools store only the request target (``/path``).
            # cpip accepts absolute URLs, so retain that public URL here.
            raw.url = current_url

            if raw.retries is not None:
                retries = raw.retries

            location = raw.get_redirect_location()

            if not location:
                return raw

            try:
                next_url = urllib.parse.urljoin(current_url, location)

                if raw.status == 303 and current_method != "HEAD":
                    current_method = "GET"
                    current_body = None
                    if not isinstance(current_headers, HTTPHeaderDict):
                        current_headers = HTTPHeaderDict(current_headers)
                    current_headers = current_headers._prepare_for_method_change()

                pool = manager.connection_from_url(current_url)
                if retries.remove_headers_on_redirect and not pool.is_same_host(
                    next_url,
                ):
                    if not isinstance(current_headers, HTTPHeaderDict):
                        current_headers = HTTPHeaderDict(current_headers)
                    for header in retries.remove_headers_on_redirect:
                        current_headers.discard(header)

                retries = retries.increment(
                    current_method,
                    current_url,
                    response=raw,
                )
            except MaxRetryError as exc:
                if retries.raise_on_redirect:
                    raw.drain_conn()
                    raise TooManyRedirectsError(
                        redact_auth_from_url(current_url),
                    ) from exc
                return raw

            except BaseException:
                raw.drain_conn()
                raise

            raw.drain_conn()
            current_url = next_url

    @staticmethod
    def open_file(request: HttpRequest) -> HttpResponseProtocol:
        try:
            file = open(url_to_path(request.url), "rb")

        except OSError as exc:
            response: Any = HTTPResponse(
                body=io.BytesIO(f"{type(exc).__name__}: {exc}".encode()),
                headers=HTTPHeaderDict(),
                status=404,
                reason=type(exc).__name__,
                preload_content=False,
                decode_content=False,
                request_url=request.url,
            )
            return response

        headers = HTTPHeaderDict()

        headers["Content-Length"] = str(os.fstat(file.fileno()).st_size)

        response: Any = HTTPResponse(
            body=file,
            headers=headers,
            status=200,
            reason="OK",
            preload_content=False,
            decode_content=False,
            request_url=request.url,
        )
        return response

    def retry_auth(
        self,
        response: HttpResponseProtocol,
        request: HttpRequest,
        headers: Mapping[str, str],
        data: bytes | None,
        timeout: Any,
        *,
        stream: bool,
    ) -> HttpResponseProtocol | None:
        if not hasattr(self.auth, "credentials_after_401"):
            return None

        username, password, credentials = self.auth.credentials_after_401(response.url)

        if username is None or password is None:
            return None

        retry_headers = dict(headers)

        retry_headers.update(
            make_headers(basic_auth=f"{username}:{password}"),
        )

        response.close()

        retry = self.request(
            request.method,
            response.url,
            headers=retry_headers,
            data=data,
            stream=stream,
            timeout=timeout,
            _auth_retry=False,
        )

        if retry.status == 401:
            logger.warning(
                "401 Error, credentials not correct for %s",
                redact_auth_from_url(response.url),
            )

        elif credentials is not None and retry.status < 400:
            try:
                logger.info("Saving credentials to keyring")
                self.auth.keyring_provider.save_auth_info(
                    credentials.url,
                    credentials.username,
                    credentials.password,
                )

            except Exception:
                logger.exception("Failed to save credentials")

        return retry
