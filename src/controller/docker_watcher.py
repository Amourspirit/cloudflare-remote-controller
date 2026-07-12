import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Protocol

import docker

from controller.config import ControllerConfig
from controller.labels import LabelValidationError, parse_container_routes
from controller.models import ContainerSnapshot, DesiredRoute, DockerEvent

LOGGER = logging.getLogger(__name__)
RELEVANT_EVENTS = {"start", "stop", "die", "destroy", "health_status: healthy"}
DOCKER_EVENT_FILTERS = {"start", "stop", "die", "destroy", "health_status"}


class DockerGateway(Protocol):
	async def running_containers(self) -> tuple[ContainerSnapshot, ...]: ...

	def events(self) -> AsyncIterator[DockerEvent]: ...

	async def aclose(self) -> None: ...


class DockerSdkGateway:
	def __init__(self, socket_path: str, *, timeout: int = 30) -> None:
		self._client = docker.DockerClient(
			base_url=f"unix://{socket_path}", timeout=timeout
		)

	async def running_containers(self) -> tuple[ContainerSnapshot, ...]:
		containers = await asyncio.to_thread(
			self._client.containers.list, filters={"status": "running"}
		)
		return tuple(
			ContainerSnapshot(
				id=container.id,
				labels=dict(container.labels or {}),
			)
			for container in containers
		)

	async def events(self) -> AsyncIterator[DockerEvent]:
		stream = self._client.events(
			decode=True,
			filters={"type": "container", "event": sorted(DOCKER_EVENT_FILTERS)},
		)
		try:
			while True:
				raw_event = await asyncio.to_thread(next, stream, None)
				if raw_event is None:
					return
				event = self._parse_event(raw_event)
				if event is not None:
					yield event
		finally:
			close = getattr(stream, "close", None)
			if close is not None:
				close()

	async def aclose(self) -> None:
		await asyncio.to_thread(self._client.close)

	@staticmethod
	def _parse_event(raw_event: Any) -> DockerEvent | None:
		if not isinstance(raw_event, dict):
			return None
		container_id = raw_event.get("id")
		action = raw_event.get("Action") or raw_event.get("status")
		if not isinstance(container_id, str) or not isinstance(action, str):
			return None
		return DockerEvent(container_id=container_id, action=action)


def routes_from_containers(
	containers: tuple[ContainerSnapshot, ...], config: ControllerConfig
) -> tuple[DesiredRoute, ...]:
	routes: list[DesiredRoute] = []
	for container in containers:
		try:
			routes.extend(parse_container_routes(container.id, container.labels, config))
		except LabelValidationError as error:
			LOGGER.error("Ignoring invalid route labels on container %s: %s", container.id, error)
	return tuple(routes)


class DockerEventWatcher:
	def __init__(self, gateway: DockerGateway, *, debounce_seconds: float) -> None:
		self._gateway = gateway
		self._debounce_seconds = debounce_seconds

	async def watch(
		self,
		on_change: Callable[[], Awaitable[None]],
		stop_event: asyncio.Event,
	) -> None:
		reconnect_delay = 1.0
		while not stop_event.is_set():
			try:
				await self.process_events(self._gateway.events(), on_change, stop_event)
				reconnect_delay = 1.0
			except asyncio.CancelledError:
				raise
			except Exception:
				LOGGER.exception("Docker event stream failed; reconnecting")
				try:
					await asyncio.wait_for(stop_event.wait(), timeout=reconnect_delay)
				except TimeoutError:
					reconnect_delay = min(reconnect_delay * 2, 30)

	async def process_events(
		self,
		events: AsyncIterator[DockerEvent],
		on_change: Callable[[], Awaitable[None]],
		stop_event: asyncio.Event,
	) -> None:
		pending: dict[str, asyncio.Task[None]] = {}
		try:
			async for event in events:
				if stop_event.is_set():
					return
				if event.action not in RELEVANT_EVENTS:
					continue
				previous = pending.pop(event.container_id, None)
				if previous is not None:
					previous.cancel()
				task = asyncio.create_task(self._debounced_change(on_change))
				pending[event.container_id] = task
		finally:
			if pending:
				await asyncio.gather(*pending.values(), return_exceptions=True)

	async def _debounced_change(
		self, on_change: Callable[[], Awaitable[None]]
	) -> None:
		await asyncio.sleep(self._debounce_seconds)
		await on_change()
