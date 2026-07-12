import asyncio

from controller.config import ControllerConfig
from controller.docker_watcher import DockerEventWatcher, routes_from_containers
from controller.models import ContainerSnapshot, DockerEvent


def config() -> ControllerConfig:
	return ControllerConfig.model_validate(
		{
			"CF_API_TOKEN": "token",
			"CF_ZONE_ID": "zone-1",
			"CF_TUNNEL_CNAME": "tunnel.cfargotunnel.com",
		}
	)


class FakeGateway:
	def __init__(self, events: tuple[DockerEvent, ...] = ()) -> None:
		self._events = events

	async def running_containers(self) -> tuple[ContainerSnapshot, ...]:
		return ()

	async def events(self):
		for event in self._events:
			yield event

	async def aclose(self) -> None:
		pass


def test_snapshot_discovery_isolates_invalid_container_labels() -> None:
	containers = (
		ContainerSnapshot(
			"valid",
			{"cfroute.enabled": "true", "cfroute.names": "app.example.com"},
		),
		ContainerSnapshot(
			"invalid",
			{"cfroute.enabled": "true", "cfroute.names": "not-a-domain"},
		),
		ContainerSnapshot("disabled", {}),
	)

	routes = routes_from_containers(containers, config())

	assert len(routes) == 1
	assert routes[0].owner.container_id == "valid"


def test_events_are_filtered_and_debounced_per_container() -> None:
	async def scenario() -> int:
		gateway = FakeGateway()
		watcher = DockerEventWatcher(gateway, debounce_seconds=0)
		calls = 0

		async def on_change() -> None:
			nonlocal calls
			calls += 1

		async def events():
			yield DockerEvent("one", "start")
			yield DockerEvent("one", "die")
			yield DockerEvent("two", "unpause")

		await watcher.process_events(events(), on_change, asyncio.Event())
		return calls

	assert asyncio.run(scenario()) == 1


def test_distinct_container_events_trigger_distinct_reconciliations() -> None:
	async def scenario() -> int:
		gateway = FakeGateway()
		watcher = DockerEventWatcher(gateway, debounce_seconds=0)
		calls = 0

		async def on_change() -> None:
			nonlocal calls
			calls += 1

		async def events():
			yield DockerEvent("one", "start")
			yield DockerEvent("two", "start")

		await watcher.process_events(events(), on_change, asyncio.Event())
		return calls

	assert asyncio.run(scenario()) == 2
