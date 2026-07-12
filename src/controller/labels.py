import re
from collections.abc import Mapping

from controller.config import ControllerConfig
from controller.models import DesiredRoute, RouteKey, RouteOwner

ENABLED_LABEL = "cfroute.enabled"
NAMES_LABEL = "cfroute.names"
ZONE_ID_LABEL = "cfroute.zone_id"
TUNNEL_CNAME_LABEL = "cfroute.tunnel_cname"
PROXIED_LABEL = "cfroute.proxied"
DELETE_ON_STOP_LABEL = "cfroute.delete_on_stop"

COMPOSE_PROJECT_LABEL = "com.docker.compose.project"
COMPOSE_SERVICE_LABEL = "com.docker.compose.service"

_NAME_SPLITTER = re.compile(r"[,;\s]+")
_DNS_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


class LabelValidationError(ValueError):
	pass


def parse_container_routes(
	container_id: str,
	labels: Mapping[str, str],
	config: ControllerConfig,
) -> tuple[DesiredRoute, ...]:
	if not _parse_bool(labels.get(ENABLED_LABEL, "false"), ENABLED_LABEL):
		return ()

	names = _parse_names(labels.get(NAMES_LABEL, ""))
	zone_id = _override_or_default(labels.get(ZONE_ID_LABEL), config.cf_zone_id)
	target = _override_or_default(
		labels.get(TUNNEL_CNAME_LABEL), config.cf_tunnel_cname
	)
	if zone_id is None:
		raise LabelValidationError(
			f"{ZONE_ID_LABEL} is required when CF_ZONE_ID is not configured"
		)
	if target is None:
		raise LabelValidationError(
			f"{TUNNEL_CNAME_LABEL} is required when CF_TUNNEL_CNAME is not configured"
		)

	owner = RouteOwner(
		container_id=container_id,
		compose_project=labels.get(COMPOSE_PROJECT_LABEL),
		compose_service=labels.get(COMPOSE_SERVICE_LABEL),
	)
	proxied = _parse_bool(labels.get(PROXIED_LABEL, "true"), PROXIED_LABEL)
	delete_on_stop = _parse_bool(
		labels.get(DELETE_ON_STOP_LABEL, "true"), DELETE_ON_STOP_LABEL
	)
	return tuple(
		DesiredRoute(
			key=RouteKey(zone_id=zone_id, name=name),
			target=target,
			proxied=proxied,
			delete_on_stop=delete_on_stop,
			owner=owner,
		)
		for name in names
	)


def _parse_names(value: str) -> tuple[str, ...]:
	names: list[str] = []
	seen: set[str] = set()
	for raw_name in _NAME_SPLITTER.split(value.strip()):
		if not raw_name:
			continue
		name = raw_name.lower().rstrip(".")
		_validate_dns_name(name)
		if name not in seen:
			names.append(name)
			seen.add(name)
	if not names:
		raise LabelValidationError(f"{NAMES_LABEL} must contain at least one DNS name")
	return tuple(names)


def _validate_dns_name(name: str) -> None:
	if len(name) > 253 or "." not in name:
		raise LabelValidationError(f"invalid DNS name in {NAMES_LABEL}: {name!r}")
	if any(not _DNS_LABEL.fullmatch(part) for part in name.split(".")):
		raise LabelValidationError(f"invalid DNS name in {NAMES_LABEL}: {name!r}")


def _parse_bool(value: str, label: str) -> bool:
	normalized = value.strip().lower()
	if normalized == "true":
		return True
	if normalized == "false":
		return False
	raise LabelValidationError(f"{label} must be true or false")


def _override_or_default(override: str | None, default: str | None) -> str | None:
	if override is not None:
		normalized = override.strip()
		if normalized:
			return normalized
	return default
