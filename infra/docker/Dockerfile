# Multi-stage build shared by every service; SERVICE selects the app module.
FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build
RUN pip install --no-cache-dir uv
COPY pyproject.toml README.md ./
COPY libs ./libs
COPY services ./services
RUN uv pip install --system --no-cache .

# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/usr/local/bin:$PATH"

RUN useradd --create-home --uid 10001 appuser
WORKDIR /app

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --chown=appuser:appuser . /app

USER appuser
EXPOSE 8000

# SERVICE is overridden per service in docker-compose / k8s.
ENV SERVICE=gateway
CMD ["sh", "-c", "uvicorn services.${SERVICE}.main:app --host 0.0.0.0 --port 8000"]
