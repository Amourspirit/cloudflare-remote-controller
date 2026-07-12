import argparse
import asyncio
import logging
import signal
import sys
import time
from pathlib import Path

from pydantic import ValidationError

from controller.cloudflare_api import CloudflareClient
from controller.config import ControllerConfig
from controller.docker_watcher import (
	DockerEventWatcher,
	DockerSdkGateway,
	routes_from_containers,
)
from controller.reconciler import ActionKind, Reconciler

LOGGER = logging.getLogger(__name__)


async def run_controller(config: ControllerConfig) -> None:
	stop_event = asyncio.Event()
	loop = asyncio.get_running_loop()
	for signum in (signal.SIGTERM, signal.SIGINT):
		loop.add_signal_handler(signum, stop_event.set)

	docker_gateway = DockerSdkGateway(str(config.docker_socket_path))
	watcher = DockerEventWatcher(
		docker_gateway, debounce_seconds=config.event_debounce_ms / 1000
	)
	reconcile_lock = asyncio.Lock()
	known_zone_ids = config.managed_zone_ids

	async with CloudflareClient(
		config.cf_api_token.get_secret_value(),
		base_url=config.cloudflare_api_base_url,
		timeout=config.cloudflare_timeout_seconds,
		max_attempts=config.cloudflare_max_attempts,
	) as cloudflare:
		reconciler = Reconciler(
			cloudflare, allow_target_mismatch=config.allow_target_mismatch
		)

		async def reconcile() -> None:
			if reconcile_lock.locked():
				return
			async with reconcile_lock:
				containers = await docker_gateway.running_containers()
				routes = routes_from_containers(containers, config)
				known_zone_ids.update(route.key.zone_id for route in routes)
				result = await reconciler.reconcile(
					routes, managed_zone_ids=known_zone_ids
				)
				for action in result.actions:
					log = LOGGER.error if action.kind == ActionKind.FAILED else LOGGER.info
					log(
						"reconcile action=%s zone=%s name=%s detail=%s",
						action.kind,
						action.key.zone_id,
						action.key.name,
						action.detail,
					)
				config.heartbeat_path.touch()

		async def periodic_reconcile() -> None:
			while not stop_event.is_set():
				try:
					await asyncio.wait_for(
						stop_event.wait(), timeout=config.reconcile_interval_seconds
					)
				except TimeoutError:
					await reconcile()

		try:
			await reconcile()
			tasks = [
				asyncio.create_task(watcher.watch(reconcile, stop_event)),
				asyncio.create_task(periodic_reconcile()),
			]
			try:
				await stop_event.wait()
			finally:
				for task in tasks:
					task.cancel()
				await asyncio.gather(*tasks, return_exceptions=True)
		finally:
			await docker_gateway.aclose()


def healthcheck(path: Path, *, max_age_seconds: float) -> bool:
	try:
		return time.time() - path.stat().st_mtime <= max_age_seconds
	except OSError:
		return False


def main() -> int:
	parser = argparse.ArgumentParser()
	parser.add_argument("--healthcheck", action="store_true")
	args = parser.parse_args()
	try:
		config = ControllerConfig()
	except ValidationError as error:
		print(f"Invalid controller configuration: {error}", file=sys.stderr)
		return 2

	if args.healthcheck:
		max_age = max(config.reconcile_interval_seconds * 2, 60)
		return 0 if healthcheck(config.heartbeat_path, max_age_seconds=max_age) else 1

	logging.basicConfig(
		level=config.log_level,
		format="%(asctime)s %(levelname)s %(name)s %(message)s",
	)
	try:
		asyncio.run(run_controller(config))
	except KeyboardInterrupt:
		return 0
	except Exception:
		LOGGER.exception("Controller stopped unexpectedly")
		return 1
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
