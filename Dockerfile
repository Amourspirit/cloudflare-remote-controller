FROM python:3.13.5-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m pip install --no-cache-dir .

STOPSIGNAL SIGTERM

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD ["python", "-m", "controller.main", "--healthcheck"]

CMD ["cfroute-controller"]