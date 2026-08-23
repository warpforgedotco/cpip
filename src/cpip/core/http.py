"""Dependency-free structural contracts for HTTP clients and responses."""

from __future__ import annotations

from typing import Any, Protocol


class HttpResponse(Protocol):
    status_code: int
    reason: str
    url: str
    headers: Any
    raw: Any
    from_cache: bool

    @property
    def content(self) -> bytes: ...

    @property
    def text(self) -> str: ...

    def raise_for_status(self) -> None: ...

    def close(self) -> None: ...


class HttpSession(Protocol):
    auth: Any
    cache: Any

    def get(self, url: str, **kwargs: Any) -> HttpResponse: ...

    def head(self, url: str, **kwargs: Any) -> HttpResponse: ...
