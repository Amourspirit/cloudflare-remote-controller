# AGENTS.md

## Project overview

Docker controller that discovers labeled containers and converges Cloudflare CNAME records to their declared routes. Single Python package in `src/controller/`.

## Tech stack

- Python 3.13, managed with `uv`
- httpx (async HTTP), docker SDK, pydantic-settings for config
- Tests: pytest (unit only), ruff (lint), mypy (strict)

## Commands

```sh
uv sync --extra dev            # install all deps including dev
uv run pytest                  # run all tests
uv run pytest tests/unit/test_labels.py -k test_name  # run single test
uv run ruff check src tests    # lint
uv run mypy src                # typecheck (strict mode)
docker build -t cloudflare-remote-controller .
docker compose --profile controller up -d --build  # run controller
```

## Architecture

Entry point: `src/controller/main.py:main` → `run_controller()` async loop.

Key modules:
- `config.py` – `ControllerConfig` (pydantic-settings), loads `.env` + env vars, case-sensitive aliases
- `docker_watcher.py` – watches Docker events, debounces, extracts routes from container labels
- `labels.py` – parses `cfroute.*` labels into `DesiredRoute` objects
- `ownership.py` – builds ownership snapshot; groups conflicting vs consistent claims
- `reconciler.py` – core logic: lists CF records, creates/updates/deletes based on ownership
- `cloudflare_api.py` – async Cloudflare client with retry (tenacity), versioned record comments
- `models.py` – frozen dataclasses: `RouteKey`, `DesiredRoute`, `DnsRecord`, etc.

## Invariants (do not break)

- **Unmarked records are never touched.** Only records with this controller's versioned comment are eligible for update or deletion.
- **Conflicting claims skip.** If two containers claim the same name with different target/proxied/delete_on_stop, that name is skipped (not reconciled).
- **DNS mutation logic lives in reconciler.** Do not move it to other modules.

## Testing rules

- Tests use **fake Docker gateways** and `httpx.MockTransport` – never real Cloudflare credentials or Docker sockets.
- Add unit coverage for any behavior change.
- All tests in `tests/unit/`.

## Conventions

- Frozen dataclasses with `slots=True` for models
- `StrEnum` for action/kind enums
- Tab indentation in Python files
- Env var aliases in `ControllerConfig` match the uppercase names (e.g., `CF_API_TOKEN`)
- Config loads `.env` file automatically via pydantic-settings
