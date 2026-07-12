# Contributing

## Development setup

Python 3.13, `uv`, and Docker are required.

```sh
uv sync --extra dev
uv run pytest
uv run ruff check src tests
uv run mypy src
docker build -t cloudflare-remote-controller .
```

Add focused unit coverage for behavior changes. Automated tests must use fake Docker gateways and `httpx.MockTransport`; do not use real Cloudflare credentials in tests.

Keep DNS mutation decisions in the reconciler and preserve the rule that unmarked Cloudflare records are never changed or deleted.