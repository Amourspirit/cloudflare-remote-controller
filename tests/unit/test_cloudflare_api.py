import asyncio
import json

import httpx
import pytest

from controller.cloudflare_api import (
    CloudflareApiError,
    CloudflareClient,
    managed_comment,
    managed_delete_policy,
)
from controller.models import DesiredRoute, RouteKey, RouteOwner


def route(*, delete_on_stop: bool = True) -> DesiredRoute:
    return DesiredRoute(
        key=RouteKey(zone_id="zone-1", name="app.example.com"),
        target="tunnel.cfargotunnel.com",
        proxied=True,
        delete_on_stop=delete_on_stop,
        owner=RouteOwner(container_id="container-1"),
    )


def record_payload(record_id: str) -> dict[str, object]:
    return {
        "id": record_id,
        "type": "CNAME",
        "name": "app.example.com",
        "content": "tunnel.cfargotunnel.com",
        "proxied": True,
        "comment": managed_comment(True),
    }


def run(coroutine: object) -> object:
    return asyncio.run(coroutine)  # type: ignore[arg-type]


def test_managed_comment_round_trip_is_strict() -> None:
    assert managed_delete_policy(managed_comment(True)) is True
    assert managed_delete_policy(managed_comment(False)) is False
    assert managed_delete_policy("managed-by=someone-else") is None
    assert managed_delete_policy(None) is None


def test_create_cname_sends_marker_and_authorization() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers["Authorization"]
        captured["path"] = request.url.path
        captured["payload"] = json.loads(request.content)
        return httpx.Response(200, json={"success": True, "result": record_payload("1")})

    async def scenario() -> None:
        async with CloudflareClient(
            "secret-token", transport=httpx.MockTransport(handler)
        ) as client:
            created = await client.create_cname(route(delete_on_stop=False))
            assert created.id == "1"

    run(scenario())
    assert captured["authorization"] == "Bearer secret-token"
    assert captured["path"] == "/client/v4/zones/zone-1/dns_records"
    assert captured["payload"] == {
        "type": "CNAME",
        "name": "app.example.com",
        "content": "tunnel.cfargotunnel.com",
        "proxied": True,
        "comment": managed_comment(False),
    }


def test_list_cname_records_follows_pagination() -> None:
    pages: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        pages.append(page)
        return httpx.Response(
            200,
            json={
                "success": True,
                "result": [record_payload(str(page))],
                "result_info": {"total_pages": 2},
            },
        )

    async def scenario() -> None:
        async with CloudflareClient(
            "token", transport=httpx.MockTransport(handler)
        ) as client:
            records = await client.list_cname_records("zone-1")
            assert [record.id for record in records] == ["1", "2"]

    run(scenario())
    assert pages == [1, 2]


def test_retries_retryable_status_without_exposing_token() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, json={"success": False})
        return httpx.Response(200, json={"success": True, "result": []})

    async def scenario() -> None:
        async with CloudflareClient(
            "secret-token",
            transport=httpx.MockTransport(handler),
            retry_min_seconds=0,
            retry_max_seconds=0,
        ) as client:
            assert await client.list_cname_records("zone-1") == ()

    run(scenario())
    assert attempts == 2


def test_does_not_retry_non_retryable_api_error() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            400,
            json={"success": False, "errors": [{"message": "bad request"}]},
        )

    async def scenario() -> None:
        async with CloudflareClient(
            "secret-token", transport=httpx.MockTransport(handler)
        ) as client:
            with pytest.raises(CloudflareApiError) as caught:
                await client.list_cname_records("zone-1")
            assert "secret-token" not in str(caught.value)

    run(scenario())
    assert attempts == 1