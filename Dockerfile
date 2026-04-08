# ─────────────────────────────────────────────────────────────────────────────
# AUREON — Multi-stage Dockerfile
#
# Stages:
#   base   — Python + system deps
#   deps   — pip install (cached layer)
#   test   — runs the test suite (used in CI)
#   api    — production FastAPI server  (default)
#   bot    — trading bot daemon
# ─────────────────────────────────────────────────────────────────────────────

ARG PYTHON_VERSION=3.11

# ── base ──────────────────────────────────────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim AS base

WORKDIR /app

# System packages needed by web3 / cryptography
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libssl-dev \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1


# ── deps ──────────────────────────────────────────────────────────────────────
FROM base AS deps

COPY requirements.txt .
RUN pip install -r requirements.txt


# ── test ──────────────────────────────────────────────────────────────────────
FROM deps AS test

COPY . .
RUN pytest --tb=short -q


# ── api (production FastAPI server) ───────────────────────────────────────────
FROM deps AS api

COPY . .

# vault/ is git-ignored; create the directory so wallet.json can be mounted
RUN mkdir -p vault logs

EXPOSE 8010

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8010/health')"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8010", "--workers", "1"]


# ── bot (trading daemon) ──────────────────────────────────────────────────────
FROM deps AS bot

COPY . .

RUN mkdir -p vault logs

# Default: paper trading. Set TRADE_MODE=live in .env or compose to go live.
CMD ["sh", "-c", \
     "if [ \"$TRADE_MODE\" = \"live\" ]; then python trade.py --live; \
      else python trade.py; fi"]
