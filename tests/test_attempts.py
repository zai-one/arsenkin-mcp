from __future__ import annotations

import sqlite3
from pathlib import Path

import httpx
import pytest

from zai_arsenkin.admission import PolicyStore
from zai_arsenkin.runtime import AdmittedHttpClient
from zai_arsenkin.transport import ProviderAdmissionDenied, ProviderTransientHttpError, RetryPolicy


@pytest.mark.parametrize("idempotent,expected", [(True, 3), (False, 1)])
async def test_actual_transport_counts_read_retries_and_never_retries_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, idempotent: bool, expected: int
):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(503, json={"errors": "temporary"})

    original = httpx.AsyncClient
    monkeypatch.setattr(
        "zai_arsenkin.transport.httpx.AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )
    store = PolicyStore(tmp_path / "state.sqlite", "default", 10, 10)
    store.begin("execution", "request", "alice", "hash")
    client = AdmittedHttpClient(store, "execution", "alice")
    client.retry = RetryPolicy(attempts=3, base_delay_seconds=0)
    with pytest.raises(ProviderTransientHttpError):
        await client.request("POST", "https://example.test/fixture", idempotent=idempotent)
    assert len(calls) == expected
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == expected


async def test_retry_quota_is_enforced_before_next_http_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(503, json={})

    original = httpx.AsyncClient
    monkeypatch.setattr(
        "zai_arsenkin.transport.httpx.AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )
    store = PolicyStore(tmp_path / "state.sqlite", "default", 1, 1)
    store.begin("execution", "request", "alice", "hash")
    client = AdmittedHttpClient(store, "execution", "alice")
    client.retry = RetryPolicy(attempts=3, base_delay_seconds=0)
    with pytest.raises(ProviderAdmissionDenied):
        await client.request("POST", "https://example.test/fixture", idempotent=True)
    assert len(calls) == 1
