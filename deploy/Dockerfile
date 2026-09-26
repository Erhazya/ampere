# The demo's image: the API, which also serves the built dashboard (ADR 018 and 020).
# From the repository root: docker build --file deploy/Dockerfile --tag ampere .

# The Python base, shared by the build and the image that runs. Its standard library is
# compiled once here (pip excluded): the user of the image cannot write .pyc files, and every
# health check would otherwise compile urllib again, for 0.8 s of processor time.
FROM python:3.13-slim@sha256:7c61056e61ac89e852de05f3dc6fa51a6dd2181797bceed46aa725dd7cb2cd3b AS base
RUN python -m compileall -q -x site-packages /usr/local/lib/python3.13

# The dashboard, built with Node 24, the version of web/.node-version.
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
FROM base AS python
COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev --no-install-project
COPY README.md LICENSE ./
COPY src/ src/
RUN uv sync --locked --no-dev --no-editable

# The image that runs: the base, the environment and the dashboard, and a user without rights.
FROM base
LABEL org.opencontainers.image.source="https://github.com/Erhazya/ampere" \
      org.opencontainers.image.description="Ampère: the API and the dashboard of the energy digital twin" \
      org.opencontainers.image.licenses="MIT"
RUN groupadd --gid 10001 ampere \
    && useradd --uid 10001 --gid ampere --no-create-home --shell /usr/sbin/nologin ampere
COPY --from=python /app/.venv /app/.venv
COPY --from=dashboard /web/dist /app/dashboard
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
WORKDIR /app
USER ampere
EXPOSE 8000
# A GET, since the API answers HEAD with 405 (ADR 018).
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --start-interval=2s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"]
# Every interface of the container; the deployment decides which host address to publish on.
# No documentation pages: they load their scripts from a CDN, which the demo's CSP refuses (ADR 021).
CMD ["ampere", "api", "--host", "0.0.0.0", "--port", "8000", "--dashboard", "/app/dashboard", "--no-docs"]
