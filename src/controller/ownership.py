from dataclasses import dataclass

from controller.models import DesiredRoute, RouteKey, RouteOwner


@dataclass(frozen=True, slots=True)
class RouteClaim:
	route: DesiredRoute
	owners: frozenset[RouteOwner]


@dataclass(frozen=True, slots=True)
class OwnershipSnapshot:
	claims: dict[RouteKey, RouteClaim]
	conflicts: dict[RouteKey, tuple[DesiredRoute, ...]]


def build_ownership_snapshot(routes: tuple[DesiredRoute, ...]) -> OwnershipSnapshot:
	grouped: dict[RouteKey, list[DesiredRoute]] = {}
	for route in routes:
		grouped.setdefault(route.key, []).append(route)

	claims: dict[RouteKey, RouteClaim] = {}
	conflicts: dict[RouteKey, tuple[DesiredRoute, ...]] = {}
	for key, candidates in grouped.items():
		configurations = {
			(candidate.target, candidate.proxied, candidate.delete_on_stop)
			for candidate in candidates
		}
		if len(configurations) != 1:
			conflicts[key] = tuple(candidates)
			continue
		claims[key] = RouteClaim(
			route=candidates[0],
			owners=frozenset(candidate.owner for candidate in candidates),
		)
	return OwnershipSnapshot(claims=claims, conflicts=conflicts)
