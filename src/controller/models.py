from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RouteKey:
	zone_id: str
	name: str


@dataclass(frozen=True, slots=True)
class RouteOwner:
	container_id: str
	compose_project: str | None = None
	compose_service: str | None = None


@dataclass(frozen=True, slots=True)
class DesiredRoute:
	key: RouteKey
	target: str
	proxied: bool
	delete_on_stop: bool
	owner: RouteOwner


@dataclass(frozen=True, slots=True)
class DnsRecord:
	id: str
	key: RouteKey
	record_type: str
	target: str
	proxied: bool
	comment: str | None = None


@dataclass(frozen=True, slots=True)
class ContainerSnapshot:
	id: str
	labels: dict[str, str]


@dataclass(frozen=True, slots=True)
class DockerEvent:
	container_id: str
	action: str
