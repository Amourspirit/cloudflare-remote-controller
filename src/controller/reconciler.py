from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from controller.cloudflare_api import managed_comment, managed_delete_policy
from controller.models import DnsRecord, DesiredRoute, RouteKey
from controller.ownership import build_ownership_snapshot


class CloudflareGateway(Protocol):
	async def list_records(self, zone_id: str) -> tuple[DnsRecord, ...]: ...

	async def create_cname(self, route: DesiredRoute) -> DnsRecord: ...

	async def update_cname(
		self, record_id: str, route: DesiredRoute
	) -> DnsRecord: ...

	async def delete_record(self, zone_id: str, record_id: str) -> None: ...


class ActionKind(StrEnum):
	CREATED = "created"
	UPDATED = "updated"
	DELETED = "deleted"
	UNCHANGED = "unchanged"
	SKIPPED = "skipped"
	FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ReconcileAction:
	kind: ActionKind
	key: RouteKey
	detail: str = ""


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
	actions: tuple[ReconcileAction, ...]

	@property
	def failures(self) -> tuple[ReconcileAction, ...]:
		return tuple(action for action in self.actions if action.kind == ActionKind.FAILED)


class Reconciler:
	def __init__(
		self, cloudflare: CloudflareGateway, *, allow_target_mismatch: bool = False
	) -> None:
		self._cloudflare = cloudflare
		self._allow_target_mismatch = allow_target_mismatch

	async def reconcile(
		self, routes: tuple[DesiredRoute, ...], *, managed_zone_ids: set[str]
	) -> ReconciliationResult:
		ownership = build_ownership_snapshot(routes)
		zone_ids = managed_zone_ids | {key.zone_id for key in ownership.claims}
		zone_ids |= {key.zone_id for key in ownership.conflicts}
		actual_by_zone: dict[str, tuple[DnsRecord, ...]] = {}
		failed_zones: set[str] = set()
		actions: list[ReconcileAction] = []

		for zone_id in sorted(zone_ids):
			try:
				actual_by_zone[zone_id] = await self._cloudflare.list_records(zone_id)
			except Exception as error:
				failed_zones.add(zone_id)
				actions.append(
					ReconcileAction(
						ActionKind.FAILED,
						RouteKey(zone_id, "*"),
						f"failed to list records: {error}",
					)
				)

		for key in ownership.conflicts:
			actions.append(
				ReconcileAction(ActionKind.SKIPPED, key, "conflicting active claims")
			)

		for key, claim in ownership.claims.items():
			if key.zone_id in failed_zones:
				continue
			records = [
				record
				for record in actual_by_zone.get(key.zone_id, ())
				if record.key.name == key.name
			]
			if not records:
				await self._apply_create(claim.route, actions)
				continue
			if len(records) > 1:
				actions.append(
					ReconcileAction(ActionKind.SKIPPED, key, "multiple records use this name")
				)
				continue
			await self._reconcile_existing(records[0], claim.route, actions)

		desired_keys = set(ownership.claims) | set(ownership.conflicts)
		for zone_records in actual_by_zone.values():
			for record in zone_records:
				if record.key in desired_keys or record.record_type != "CNAME":
					continue
				if managed_delete_policy(record.comment) is not True:
					continue
				try:
					await self._cloudflare.delete_record(record.key.zone_id, record.id)
					actions.append(ReconcileAction(ActionKind.DELETED, record.key))
				except Exception as error:
					actions.append(
						ReconcileAction(ActionKind.FAILED, record.key, str(error))
					)
		return ReconciliationResult(tuple(actions))

	async def _apply_create(
		self, route: DesiredRoute, actions: list[ReconcileAction]
	) -> None:
		try:
			await self._cloudflare.create_cname(route)
			actions.append(ReconcileAction(ActionKind.CREATED, route.key))
		except Exception as error:
			actions.append(ReconcileAction(ActionKind.FAILED, route.key, str(error)))

	async def _reconcile_existing(
		self,
		record: DnsRecord,
		route: DesiredRoute,
		actions: list[ReconcileAction],
	) -> None:
		if record.record_type != "CNAME":
			actions.append(
				ReconcileAction(ActionKind.SKIPPED, route.key, "non-CNAME collision")
			)
			return
		if managed_delete_policy(record.comment) is None:
			actions.append(
				ReconcileAction(ActionKind.SKIPPED, route.key, "record is not managed")
			)
			return

		target_mismatch = record.target != route.target or record.proxied != route.proxied
		marker_mismatch = record.comment != managed_comment(route.delete_on_stop)
		if target_mismatch and not self._allow_target_mismatch:
			actions.append(
				ReconcileAction(ActionKind.SKIPPED, route.key, "target mismatch")
			)
			return
		if not target_mismatch and not marker_mismatch:
			actions.append(ReconcileAction(ActionKind.UNCHANGED, route.key))
			return
		try:
			await self._cloudflare.update_cname(record.id, route)
			actions.append(ReconcileAction(ActionKind.UPDATED, route.key))
		except Exception as error:
			actions.append(ReconcileAction(ActionKind.FAILED, route.key, str(error)))
