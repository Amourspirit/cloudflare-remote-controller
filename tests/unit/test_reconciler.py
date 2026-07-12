import asyncio
from dataclasses import replace

from controller.cloudflare_api import managed_comment
from controller.models import DnsRecord, DesiredRoute, RouteKey, RouteOwner
from controller.ownership import build_ownership_snapshot
from controller.reconciler import ActionKind, Reconciler


class FakeCloudflare:
	def __init__(self, records: tuple[DnsRecord, ...] = ()) -> None:
		self.records = records
		self.created: list[DesiredRoute] = []
		self.updated: list[tuple[str, DesiredRoute]] = []
		self.deleted: list[tuple[str, str]] = []

	async def list_records(self, zone_id: str) -> tuple[DnsRecord, ...]:
		return tuple(record for record in self.records if record.key.zone_id == zone_id)

	async def create_cname(self, route: DesiredRoute) -> DnsRecord:
		self.created.append(route)
		return dns_record(route)

	async def update_cname(self, record_id: str, route: DesiredRoute) -> DnsRecord:
		self.updated.append((record_id, route))
		return dns_record(route, record_id=record_id)

	async def delete_record(self, zone_id: str, record_id: str) -> None:
		self.deleted.append((zone_id, record_id))


class FailingListCloudflare(FakeCloudflare):
	async def list_records(self, zone_id: str) -> tuple[DnsRecord, ...]:
		raise RuntimeError("zone unavailable")


def desired(
	name: str = "app.example.com",
	*,
	container_id: str = "container-1",
	target: str = "tunnel.cfargotunnel.com",
	delete_on_stop: bool = True,
) -> DesiredRoute:
	return DesiredRoute(
		key=RouteKey("zone-1", name),
		target=target,
		proxied=True,
		delete_on_stop=delete_on_stop,
		owner=RouteOwner(container_id),
	)


def dns_record(
	route: DesiredRoute,
	*,
	record_id: str = "record-1",
	record_type: str = "CNAME",
	comment: str | None = None,
) -> DnsRecord:
	return DnsRecord(
		id=record_id,
		key=route.key,
		record_type=record_type,
		target=route.target,
		proxied=route.proxied,
		comment=managed_comment(route.delete_on_stop) if comment is None else comment,
	)


def reconcile(
	cloudflare: FakeCloudflare,
	routes: tuple[DesiredRoute, ...],
	*,
	allow_target_mismatch: bool = False,
):
	return asyncio.run(
		Reconciler(
			cloudflare, allow_target_mismatch=allow_target_mismatch
		).reconcile(routes, managed_zone_ids={"zone-1"})
	)


def test_shared_identical_claims_are_consolidated() -> None:
	first = desired(container_id="one")
	second = replace(first, owner=RouteOwner("two"))

	snapshot = build_ownership_snapshot((first, second))

	assert snapshot.conflicts == {}
	assert snapshot.claims[first.key].owners == {RouteOwner("one"), RouteOwner("two")}


def test_creates_missing_record_once_for_shared_owners() -> None:
	cloudflare = FakeCloudflare()
	first = desired(container_id="one")
	second = replace(first, owner=RouteOwner("two"))

	result = reconcile(cloudflare, (first, second))

	assert cloudflare.created == [first]
	assert [action.kind for action in result.actions] == [ActionKind.CREATED]


def test_conflicting_claim_is_skipped_without_mutation() -> None:
	cloudflare = FakeCloudflare()
	first = desired(container_id="one")
	second = desired(container_id="two", target="other.cfargotunnel.com")

	result = reconcile(cloudflare, (first, second))

	assert cloudflare.created == []
	assert result.actions[0].kind == ActionKind.SKIPPED
	assert result.actions[0].detail == "conflicting active claims"


def test_unmanaged_and_non_cname_collisions_are_preserved() -> None:
	route = desired()
	unmanaged = dns_record(route, comment="user-owned")
	non_cname = dns_record(route, record_type="A", comment="user-owned")

	unmanaged_result = reconcile(FakeCloudflare((unmanaged,)), (route,))
	non_cname_result = reconcile(FakeCloudflare((non_cname,)), (route,))

	assert unmanaged_result.actions[0].detail == "record is not managed"
	assert non_cname_result.actions[0].detail == "non-CNAME collision"


def test_target_mismatch_requires_explicit_permission() -> None:
	route = desired()
	existing = replace(dns_record(route), target="old.cfargotunnel.com")
	skipped_client = FakeCloudflare((existing,))
	updated_client = FakeCloudflare((existing,))

	skipped = reconcile(skipped_client, (route,))
	updated = reconcile(updated_client, (route,), allow_target_mismatch=True)

	assert skipped.actions[0].kind == ActionKind.SKIPPED
	assert skipped_client.updated == []
	assert updated.actions[0].kind == ActionKind.UPDATED
	assert updated_client.updated == [("record-1", route)]


def test_deletes_only_marked_delete_enabled_orphans() -> None:
	deletable_route = desired("delete.example.com")
	retained_route = desired("retain.example.com", delete_on_stop=False)
	unmanaged_route = desired("user.example.com")
	cloudflare = FakeCloudflare(
		(
			dns_record(deletable_route, record_id="delete"),
			dns_record(retained_route, record_id="retain"),
			dns_record(unmanaged_route, record_id="user", comment="user-owned"),
		)
	)

	result = reconcile(cloudflare, ())

	assert cloudflare.deleted == [("zone-1", "delete")]
	assert [action.kind for action in result.actions] == [ActionKind.DELETED]


def test_matching_record_is_idempotent() -> None:
	route = desired()
	cloudflare = FakeCloudflare((dns_record(route),))

	result = reconcile(cloudflare, (route,))

	assert result.actions[0].kind == ActionKind.UNCHANGED
	assert cloudflare.created == []
	assert cloudflare.updated == []
	assert cloudflare.deleted == []


def test_failed_zone_listing_never_triggers_mutation() -> None:
	route = desired()
	cloudflare = FailingListCloudflare()

	result = reconcile(cloudflare, (route,))

	assert len(result.failures) == 1
	assert result.failures[0].key == RouteKey("zone-1", "*")
	assert cloudflare.created == []
	assert cloudflare.updated == []
	assert cloudflare.deleted == []
