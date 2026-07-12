from collections.abc import Mapping
from typing import Any

import httpx
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt
from tenacity.wait import wait_exponential

from controller.models import DnsRecord, DesiredRoute, RouteKey

DEFAULT_API_BASE_URL = "https://api.cloudflare.com/client/v4"
MANAGED_MARKER_PREFIX = "managed-by=cfroute-controller;version=1;delete-on-stop="


class CloudflareApiError(RuntimeError):
	def __init__(
		self,
		message: str,
		*,
		status_code: int | None = None,
		retryable: bool = False,
	) -> None:
		super().__init__(message)
		self.status_code = status_code
		self.retryable = retryable


class RetryableCloudflareError(CloudflareApiError):
	pass


def managed_comment(delete_on_stop: bool) -> str:
	return f"{MANAGED_MARKER_PREFIX}{str(delete_on_stop).lower()}"


def managed_delete_policy(comment: str | None) -> bool | None:
	if comment == managed_comment(True):
		return True
	if comment == managed_comment(False):
		return False
	return None


class CloudflareClient:
	def __init__(
		self,
		api_token: str,
		*,
		base_url: str = DEFAULT_API_BASE_URL,
		timeout: float = 20,
		max_attempts: int = 3,
		retry_min_seconds: float = 1,
		retry_max_seconds: float = 10,
		transport: httpx.AsyncBaseTransport | None = None,
	) -> None:
		self._max_attempts = max_attempts
		self._retry_min_seconds = retry_min_seconds
		self._retry_max_seconds = retry_max_seconds
		self._client = httpx.AsyncClient(
			base_url=f"{base_url.rstrip('/')}/",
			headers={
				"Authorization": f"Bearer {api_token}",
				"Content-Type": "application/json",
			},
			timeout=timeout,
			transport=transport,
		)

	async def __aenter__(self) -> "CloudflareClient":
		return self

	async def __aexit__(self, *args: object) -> None:
		await self.aclose()

	async def aclose(self) -> None:
		await self._client.aclose()

	async def list_cname_records(
		self, zone_id: str, *, name: str | None = None
	) -> tuple[DnsRecord, ...]:
		return await self.list_records(zone_id, name=name, record_type="CNAME")

	async def list_records(
		self,
		zone_id: str,
		*,
		name: str | None = None,
		record_type: str | None = None,
	) -> tuple[DnsRecord, ...]:
		records: list[DnsRecord] = []
		page = 1
		while True:
			params: dict[str, str | int] = {
				"per_page": 100,
				"page": page,
			}
			if record_type is not None:
				params["type"] = record_type
			if name is not None:
				params["name"] = name
			envelope = await self._request(
				"GET", f"zones/{zone_id}/dns_records", params=params
			)
			result = envelope.get("result", [])
			if not isinstance(result, list):
				raise CloudflareApiError("Cloudflare returned an invalid record list")
			records.extend(self._parse_record(zone_id, item) for item in result)

			result_info = envelope.get("result_info")
			total_pages = (
				result_info.get("total_pages", 1)
				if isinstance(result_info, Mapping)
				else 1
			)
			if page >= int(total_pages):
				return tuple(records)
			page += 1

	async def create_cname(self, route: DesiredRoute) -> DnsRecord:
		envelope = await self._request(
			"POST",
			f"zones/{route.key.zone_id}/dns_records",
			json=self._route_payload(route),
		)
		return self._parse_record(route.key.zone_id, envelope.get("result"))

	async def update_cname(self, record_id: str, route: DesiredRoute) -> DnsRecord:
		envelope = await self._request(
			"PUT",
			f"zones/{route.key.zone_id}/dns_records/{record_id}",
			json=self._route_payload(route),
		)
		return self._parse_record(route.key.zone_id, envelope.get("result"))

	async def delete_record(self, zone_id: str, record_id: str) -> None:
		await self._request("DELETE", f"zones/{zone_id}/dns_records/{record_id}")

	async def _request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
		retrying = AsyncRetrying(
			stop=stop_after_attempt(self._max_attempts),
			wait=wait_exponential(
				multiplier=self._retry_min_seconds,
				min=self._retry_min_seconds,
				max=self._retry_max_seconds,
			),
			retry=retry_if_exception_type(
				(httpx.TransportError, RetryableCloudflareError)
			),
			reraise=True,
		)
		async for attempt in retrying:
			with attempt:
				try:
					response = await self._client.request(method, url, **kwargs)
				except httpx.TransportError:
					raise
				if response.status_code == 429 or response.status_code >= 500:
					raise RetryableCloudflareError(
						f"Cloudflare request failed with HTTP {response.status_code}",
						status_code=response.status_code,
						retryable=True,
					)
				if response.is_error:
					raise CloudflareApiError(
						f"Cloudflare request failed with HTTP {response.status_code}",
						status_code=response.status_code,
					)
				try:
					envelope = response.json()
				except ValueError as error:
					raise CloudflareApiError("Cloudflare returned invalid JSON") from error
				if not isinstance(envelope, dict):
					raise CloudflareApiError("Cloudflare returned an invalid response")
				if envelope.get("success") is not True:
					errors = envelope.get("errors")
					raise CloudflareApiError(
						f"Cloudflare API reported failure: {errors!r}",
						status_code=response.status_code,
					)
				return envelope
		raise AssertionError("retry loop ended without a response")

	@staticmethod
	def _route_payload(route: DesiredRoute) -> dict[str, object]:
		return {
			"type": "CNAME",
			"name": route.key.name,
			"content": route.target,
			"proxied": route.proxied,
			"comment": managed_comment(route.delete_on_stop),
		}

	@staticmethod
	def _parse_record(zone_id: str, value: object) -> DnsRecord:
		if not isinstance(value, Mapping):
			raise CloudflareApiError("Cloudflare returned an invalid DNS record")
		try:
			return DnsRecord(
				id=str(value["id"]),
				key=RouteKey(
					zone_id=zone_id, name=str(value["name"]).lower().rstrip(".")
				),
				record_type=str(value["type"]),
				target=str(value["content"]),
				proxied=bool(value.get("proxied", False)),
				comment=str(value["comment"]) if value.get("comment") else None,
			)
		except KeyError as error:
			raise CloudflareApiError(
				f"Cloudflare DNS record is missing {error.args[0]}"
			) from error
