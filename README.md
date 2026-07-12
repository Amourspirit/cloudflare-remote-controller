# Cloudflare Remote Controller

An always-on Docker controller that discovers opted-in containers and converges Cloudflare CNAME records to their declared routes.

## How it works

The controller reads labels from all running containers on the Docker host. It reconciles their desired CNAME records at startup, after relevant Docker lifecycle events, and on a periodic interval. Cloudflare credentials stay in the controller and are never passed to workload containers.

Records created by the controller receive a versioned Cloudflare comment. Only records with that exact marker are eligible for updates or orphan cleanup. Existing unmarked records are never adopted, changed, or deleted.

## Quick start

1. Create a Cloudflare API token with Zone DNS Edit and Zone Read permissions for the managed zone.
2. Copy `.env.example` to `.env` and set `CF_API_TOKEN`, `CF_ZONE_ID`, and `CF_TUNNEL_CNAME`.
3. Start the controller:

```sh
docker compose --profile controller up -d --build
```

4. Add labels to a workload on the same Docker host:

```yaml
services:
	app:
		image: your-application
		labels:
			cfroute.enabled: "true"
			cfroute.names: "app.example.com,api.example.com"
			cfroute.proxied: "true"
			cfroute.delete_on_stop: "true"
```

The workload does not need to share a Compose project or network with the controller.

## Labels

| Label | Required | Default | Description |
| --- | --- | --- | --- |
| `cfroute.enabled` | Yes | `false` | Opts the container into route management. |
| `cfroute.names` | Yes | None | DNS names separated by commas, semicolons, or whitespace. |
| `cfroute.zone_id` | No | `CF_ZONE_ID` | Cloudflare zone override. |
| `cfroute.tunnel_cname` | No | `CF_TUNNEL_CNAME` | CNAME target override. |
| `cfroute.proxied` | No | `true` | Enables the Cloudflare proxy. |
| `cfroute.delete_on_stop` | No | `true` | Allows orphan deletion after the final owner stops. |

Names are lowercased, trailing dots are removed, and duplicates are collapsed. Boolean labels accept only `true` or `false`. Invalid labels are logged and isolated to their container.

When workloads use `cfroute.zone_id` overrides, add those zone IDs to the comma-separated `CF_MANAGED_ZONE_IDS` setting. The controller remembers override zones during its process lifetime, while the explicit list enables orphan cleanup after a controller restart.

## Configuration

| Environment variable | Default | Description |
| --- | --- | --- |
| `CF_API_TOKEN` | Required | Cloudflare API bearer token. |
| `CF_ZONE_ID` | Optional | Default zone for labels without an override. |
| `CF_MANAGED_ZONE_IDS` | Empty | Additional zones scanned for restart-safe orphan cleanup. |
| `CF_TUNNEL_CNAME` | Optional | Default CNAME target. |
| `RECONCILE_INTERVAL_SECONDS` | `120` | Full reconciliation interval. |
| `EVENT_DEBOUNCE_MS` | `500` | Per-container event debounce interval. |
| `ALLOW_TARGET_MISMATCH` | `false` | Allows updates to mismatched marked records. |
| `CLOUDFLARE_TIMEOUT_SECONDS` | `20` | Cloudflare request timeout. |
| `CLOUDFLARE_MAX_ATTEMPTS` | `3` | Attempts for transport, 429, and 5xx failures. |
| `DOCKER_SOCKET_PATH` | `/var/run/docker.sock` | Docker socket path inside the controller. |
| `LOG_LEVEL` | `INFO` | Python logging level. |

## Safety behavior

- Multiple containers may claim an identical route. The record remains until the last owner stops.
- Conflicting active claims for one name are logged and skipped.
- Target or proxy mismatches are skipped unless `ALLOW_TARGET_MISMATCH=true`.
- Even when mismatch updates are enabled, only records marked by this controller are changed.
- Partial failures are retained and retried during later reconciliation; successful operations are not rolled back.
- Marked records with `delete_on_stop=false` remain after all owners stop and across controller restarts.
- If a zone cannot be listed, no mutation is attempted for that zone.

## Sample workload

[examples/sample-app](examples/sample-app) is a label-only cross-stack fixture. It contains no Cloudflare credentials or DNS scripts.

```sh
SAMPLE_ROUTE_NAME=test.example.com docker compose \
	-f examples/sample-app/compose.yaml up -d --build
```

Stopping the sample removes its marked record when no other running container claims the same name.

## Development

```sh
uv sync --extra dev
uv run pytest
uv run ruff check src tests
uv run mypy src
docker build -t cloudflare-remote-controller .
```

## Security

Mounting the Docker socket grants highly privileged access to the host daemon, even when mounted read-only. Run the controller only from a trusted image, protect its API token, and scope the token to the smallest possible set of zones.

This project is licensed under the MIT License. See [LICENSE](LICENSE).
