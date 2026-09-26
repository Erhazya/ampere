#!/usr/bin/env bash
# Starts a container of the image and checks that it behaves as it will online (ADR 020): the
# API, the dashboard with its cache header and font type, a user without rights, the health check.
# Usage: deploy/smoke-test.sh IMAGE
set -euo pipefail

image=$1
base=http://127.0.0.1:8000

container=$(docker run --detach --publish 127.0.0.1:8000:8000 "$image")
trap 'docker rm --force "$container" > /dev/null' EXIT

fail() {
  echo "FAIL: $*" >&2
  docker logs "$container" >&2 || true
  exit 1
}

# The API answers once uvicorn has started; the attempts before that stay silent.
curl --fail --silent --retry 20 --retry-delay 1 --retry-all-errors \
  --output /dev/null "$base/healthz" || fail "no answer on /healthz"
body=$(curl --fail --silent --show-error "$base/api/healthz")
[[ $body == *'"status":"ok"'* ]] || fail "/api/healthz answered $body"

# The dashboard: its page, checked again on every visit, and a font with its real type.
headers=$(curl --fail --silent --show-error --dump-header - --output /dev/null "$base/")
grep --quiet --ignore-case '^content-type: text/html' <<< "$headers" || fail "/ is not a page"
grep --quiet --ignore-case '^cache-control: no-cache' <<< "$headers" || fail "/ is cached"
font=$(docker exec "$container" sh -c 'cd /app/dashboard && ls assets/*.woff2 | head -n 1')
headers=$(curl --fail --silent --show-error --dump-header - --output /dev/null "$base/$font")
grep --quiet --ignore-case '^content-type: font/woff2' <<< "$headers" || fail "$font has the wrong type"

# The process does not run as root.
[[ $(docker exec "$container" id -u) != 0 ]] || fail "the container runs as root"

# Docker's health check, a GET on /healthz, reports the container healthy.
status=starting
for _ in $(seq 30); do
  status=$(docker inspect --format '{{.State.Health.Status}}' "$container")
  [[ $status == healthy ]] && break
  sleep 1
done
[[ $status == healthy ]] || fail "health status: $status"

echo "OK: $image answers; memory in use: $(docker stats --no-stream --format '{{.MemUsage}}' "$container")"
