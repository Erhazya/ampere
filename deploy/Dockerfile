# The demo's image: the API, which also serves the built dashboard (ADR 018 and 020).
# From the repository root: docker build --file deploy/Dockerfile --tag ampere .

# The dashboard, built with the Node version of web/.node-version.
FROM node:24-slim@sha256:0e0ff40c39bc087845bfb27465a0df4ea419520094bc35842ff83dd8cbe6f9b6 AS dashboard
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# uv, the same version as in development and in the CI (ADR 019).
FROM ghcr.io/astral-sh/uv:0.12.18@sha256:3adc3706091ce7c2fe595e669628caedd6d951551b92b258b7e7dbe06d9440bc AS uv

# The Python environment: the locked dependencies without the development tools, then the
# project itself, installed as a package so that src/ is not needed at run time.
FROM python:3.13-slim@sha256:7c61056e61ac89e852de05f3dc6fa51a6dd2181797bceed46aa725dd7cb2cd3b AS python
COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev --no-install-project
COPY README.md LICENSE ./
COPY src/ src/
RUN uv sync --locked --no-dev --no-editable

# The image that runs: the same Python, the environment and the dashboard, nothing else.
FROM python:3.13-slim@sha256:7c61056e61ac89e852de05f3dc6fa51a6dd2181797bceed46aa725dd7cb2cd3b
LABEL org.opencontainers.image.source="https://github.com/Erhazya/ampere" \
      org.opencontainers.image.description="Ampère: the API and the dashboard of the energy digital twin" \
      org.opencontainers.image.licenses="MIT"
# /etc/mime.types, without which Python sends the dashboard's fonts as text/plain.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends media-types \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --system --uid 10001 --no-create-home ampere
COPY --from=python /app/.venv /app/.venv
COPY --from=dashboard /web/dist /app/dashboard
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
USER ampere
EXPOSE 8000
# A GET, since the API answers HEAD with 405 (ADR 018).
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --start-interval=2s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"]
# Every interface of the container: the platform publishes the port on 127.0.0.1 only.
CMD ["ampere", "api", "--host", "0.0.0.0", "--port", "8000", "--dashboard", "/app/dashboard"]
