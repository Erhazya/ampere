#!/usr/bin/env bash
# Starts a container of the image and checks that it behaves as it will online (ADR 020): the
# API without its documentation pages (ADR 021), the dashboard with its cache header and font
# type, a user other than root, the health check.
# The container gets a free port of 127.0.0.1 from Docker, so a running development API is fine.
# Usage: deploy/smoke-test.sh IMAGE
set -euo pipefail

image=$1
container=$(docker create --publish 127.0.0.1::8000 "$image")
# After a failure, the container's logs explain it; in every case, the container goes away.
trap 'status=$?
if ((status != 0)); then docker logs "$container" >&2 || true; fi
docker rm --force "$container" > /dev/null' EXIT
docker start "$container" > /dev/null
base=http://$(docker port "$container" 8000)

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

# The API answers once uvicorn has started; the attempts before that stay silent.
curl --fail --silent --retry 20 --retry-delay 1 --retry-all-errors \
  --output /dev/null "$base/healthz" || fail "no answer on /healthz"
body=$(curl --fail --silent --show-error "$base/api/healthz")
[[ $body == *'"status":"ok"'* ]] || fail "/api/healthz answered $body"

# No documentation pages online (ADR 021), but the schema stays.
status=$(curl --silent --output /dev/null --write-out '%{http_code}' "$base/api/docs")
[[ $status == 404 ]] || fail "/api/docs answered $status"
curl --fail --silent --output /dev/null "$base/api/openapi.json" || fail "no schema at /api/openapi.json"

# The dashboard: its page, checked again on every visit, and a font with its real type.
headers=$(curl --fail --silent --show-error --dump-header - --output /dev/null "$base/")
grep --quiet --ignore-case '^content-type: text/html' <<< "$headers" || fail "/ is not a page"
grep --quiet --ignore-case '^cache-control: no-cache' <<< "$headers" || fail "/ is cached"
font=$(docker exec "$container" sh -c 'cd /app/dashboard && ls assets/*.woff2 | head -n 1')
[[ -n $font ]] || fail "no .woff2 font in the build"
headers=$(curl --fail --silent --show-error --dump-header - --output /dev/null "$base/$font")
grep --quiet --ignore-case '^content-type: font/woff2' <<< "$headers" || fail "$font has the wrong type"

# The image's user is not root.
uid=$(docker exec "$container" id -u)
[[ $uid != 0 ]] || fail "the container runs as root"

# Docker's health check, a GET on /healthz, reports the container healthy.
health=starting
for _ in $(seq 30); do
  health=$(docker inspect --format '{{.State.Health.Status}}' "$container")
  [[ $health == healthy ]] && break
  sleep 1
done
[[ $health == healthy ]] || fail "health status: $health"

echo "OK: $image answers; memory in use: $(docker stats --no-stream --format '{{.MemUsage}}' "$container")"
