#!/usr/bin/env bash
# Starts the image with deploy/compose.yaml, the settings of the online demo (ADR 021): a
# read-only file system, no capabilities, and memory, CPU and process limits. Checks that it
# answers, and that a redirect keeps https behind a proxy, which shows that uvicorn trusts its
# forwarded headers. The containers go away in every case.
# Usage: deploy/compose-test.sh IMAGE [PORT]   (PORT defaults to 18101, outside the demos' ports)
set -euo pipefail

export DEMO_IMAGE=$1 DEMO_PORT=${2:-18101}
base=http://127.0.0.1:$DEMO_PORT

compose() { docker compose --file deploy/compose.yaml --project-name ampere-compose-test "$@"; }
# After a failure, the state, the logs and the health checks explain it.
trap 'status=$?
if ((status != 0)); then
  compose ps --all >&2 || true
  compose logs >&2 || true
  docker inspect --format "{{json .State.Health}}" "$(compose ps --all --quiet api)" >&2 || true
fi
compose down > /dev/null 2>&1 || true' EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

compose up --detach --wait --wait-timeout 60 || fail "the container did not become healthy"
curl --fail --silent --show-error --output /dev/null "$base/api/healthz" ||
  fail "no answer on /api/healthz"
location=$(curl --silent --output /dev/null --write-out '%{redirect_url}' \
  --header 'X-Forwarded-Proto: https' "$base/api/healthz/")
[[ $location == "https://127.0.0.1:$DEMO_PORT/api/healthz" ]] ||
  fail "/api/healthz/ redirects to '$location': uvicorn ignores the proxy's headers"

echo "OK: $DEMO_IMAGE runs with the settings of the online demo"
