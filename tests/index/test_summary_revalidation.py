"""Revalidate-then-summary: a 304 on a stale page keeps the link-free path."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from cpip.core.packaging import parse_requirement
from cpip.index.catalog_cache import cache_key, catalog_generation
from cpip.index.source_locations import SimpleIndexSource
from cpip.network.exceptions import ConnectionFailedError, NetworkConnectionError
from cpip.network.http import HttpRequest, HttpResponse, NetworkSession

import io

INDEX_URL = "https://index.invalid/simple"
PROJECT_URL = "https://index.invalid/simple/demo/"
JSON_TYPE = "application/vnd.pypi.simple.v1+json"


def page_body(versions: tuple[str, ...]) -> bytes:
    return json.dumps(
        {
            "meta": {"api-version": "1.0"},
            "name": "demo",
            "files": [
                {
                    "url": f"https://files.invalid/demo-{version}-py3-none-any.whl",
                    "filename": f"demo-{version}-py3-none-any.whl",
                    "hashes": {"sha256": "a" * 64},
                }
                for version in versions
            ],
        },
    ).encode()


class FakeIndexSession(NetworkSession):
    def __init__(self, cache_dir: str) -> None:
        super().__init__(cache=cache_dir)
        self.etag = '"v1"'
        self.body = page_body(("1.0", "2.0"))
        self.transport_calls = 0
        self.fail_with: type[BaseException] | None = None
        self.status_override: int | None = None

    def open_internal(
        self,
        request: HttpRequest,
        timeout,
        *,
        stream: bool = False,
    ) -> HttpResponse:
        self.transport_calls += 1
        if self.fail_with is not None:
            raise self.fail_with()
        if self.status_override is not None:
            return HttpResponse(
                status_code=self.status_override,
                reason="Not Found",
                url=request.url,
                headers={"Content-Type": "text/plain"},
                raw=io.BytesIO(b""),
                content_internal=b"",
                request=request,
            )
        if request.headers.get("If-None-Match") == self.etag:
            return HttpResponse(
                status_code=304,
                reason="Not Modified",
                url=request.url,
                headers={"ETag": self.etag, "Cache-Control": "max-age=600"},
                raw=io.BytesIO(b""),
                content_internal=b"",
                request=request,
            )
        return HttpResponse(
            status_code=200,
            reason="OK",
            url=request.url,
            headers={
                "Content-Type": JSON_TYPE,
                "Cache-Control": "max-age=0",
                "ETag": self.etag,
            },
            raw=io.BytesIO(self.body),
            content_internal=self.body,
            request=request,
        )


def primed_source(tmp_path: Path) -> tuple[SimpleIndexSource, FakeIndexSession]:
    session = FakeIndexSession(str(tmp_path / "http-cache"))
    source = SimpleIndexSource(INDEX_URL, session=session)
    links = source.collect_links(parse_requirement("demo"))
    assert len(links) == 2
    assert session.transport_calls == 1
    assert not source.has_fresh_cached_page(parse_requirement("demo"))
    return source, session


def stored_generation(session: FakeIndexSession) -> str:
    raw = session.cache.get_atomic(cache_key(PROJECT_URL))
    assert raw is not None
    return catalog_generation(raw)


def test_stale_page_revalidates_into_summary(tmp_path: Path) -> None:
    source, session = primed_source(tmp_path)
    generation = stored_generation(session)

    summary = source.collect_cached_catalog_summary(
        parse_requirement("demo"),
        allow_fetch=True,
    )

    assert summary is not None
    assert summary[0] == generation
    assert session.transport_calls == 2
    assert source.has_fresh_cached_page(parse_requirement("demo"))
    assert not source.page_fetch_outcomes


def test_changed_page_reparses_once(tmp_path: Path) -> None:
    source, session = primed_source(tmp_path)
    old_generation = stored_generation(session)
    session.etag = '"v2"'
    session.body = page_body(("1.0", "2.0", "3.0"))

    requirement = parse_requirement("demo")
    summary = source.collect_cached_catalog_summary(requirement, allow_fetch=True)

    assert summary is None
    assert session.transport_calls == 2
    assert stored_generation(session) != old_generation

    links = source.collect_links(requirement)
    assert len(links) == 3
    assert session.transport_calls == 2
    assert not source.page_fetch_outcomes


def test_missing_page_memoizes_empty(tmp_path: Path) -> None:
    source, session = primed_source(tmp_path)
    session.cache.delete(PROJECT_URL)
    session.fresh_cached_response_cache.clear()
    session.status_override = 404

    requirement = parse_requirement("demo")
    summary = source.collect_cached_catalog_summary(requirement, allow_fetch=True)

    assert summary is None
    assert session.transport_calls == 2
    assert source.collect_links(requirement) == []
    assert session.transport_calls == 2


def test_network_error_propagates(tmp_path: Path) -> None:
    source, session = primed_source(tmp_path)
    session.fail_with = ConnectionError

    with pytest.raises((ConnectionFailedError, NetworkConnectionError)):
        source.collect_cached_catalog_summary(
            parse_requirement("demo"),
            allow_fetch=True,
        )


def test_no_fetch_by_default(tmp_path: Path) -> None:
    source, session = primed_source(tmp_path)

    summary = source.collect_cached_catalog_summary(parse_requirement("demo"))

    assert summary is None
    assert session.transport_calls == 1


def test_provider_stays_in_record_world_after_revalidation(tmp_path: Path) -> None:
    _source, session = primed_source(tmp_path)
    from cpip.index.provider import CandidateProvider

    provider = CandidateProvider.from_options(
        index_url=INDEX_URL,
        session=session,
    )
    link_from_record = provider.link_from_catalog_record
    constructed: list[str] = []

    def counting_link_from_record(record, source_url):
        constructed.append(str(record[0]))
        return link_from_record(record, source_url)

    provider.link_from_catalog_record = counting_link_from_record

    versions = {
        summary.version
        for summary in provider.available_versions(
            parse_requirement("demo"),
        )
    }
    assert {str(version) for version in versions} == {"1.0", "2.0"}
    assert session.transport_calls == 2

    catalog = provider.package_catalog_cache[("demo", True, True)]
    assert catalog.records_by_version is not None
    assert catalog.links == ()

    candidates = provider.find_candidates(parse_requirement("demo==2.0"))
    assert [str(candidate.version) for candidate in candidates] == ["2.0"]
    assert constructed == ["https://files.invalid/demo-2.0-py3-none-any.whl"]
    assert session.transport_calls == 2
    provider.close()
