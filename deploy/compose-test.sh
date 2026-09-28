#!/usr/bin/env bash
# Starts the image with deploy/compose.yaml, the settings of the online demo (ADR 021): a
# read-only file system, no capabilities, and memory, CPU and process limits. Checks that it
# answers, and that a redirect keeps https behind a proxy, which shows that uvicorn trusts its
# forwarded headers. Checks too that the daily job writes in a data folder of its own, which the
# API reads without being able to write in it (ADR 028), and that the API serves the export of the
# Data screen once the job has written it (ADR 029). The containers and the folder go away in
# every case.
# Usage: deploy/compose-test.sh IMAGE [PORT]   (PORT defaults to 18101, outside the demos' ports)
set -euo pipefail

export DEMO_IMAGE=$1 DEMO_PORT=${2:-18101}
base=http://127.0.0.1:$DEMO_PORT
# Open to all: the job writes there as the image's user, whatever the host's users.
AMPERE_DATA_DIR=$(mktemp --directory)
export AMPERE_DATA_DIR
chmod 777 "$AMPERE_DATA_DIR"

compose() { docker compose --file deploy/compose.yaml --project-name ampere-compose-test "$@"; }
# After a failure, the state, the logs and the health checks explain it. What the daily job wrote
# belongs to the image's user, who alone can remove it.
clean_up() {
  local status=$?
  if ((status != 0)); then
    compose ps --all >&2 || true
    compose logs >&2 || true
    docker inspect --format "{{json .State.Health}}" "$(compose ps --all --quiet api)" >&2 || true
  fi
  compose run --rm --no-deps --entrypoint rm daily -r -f /data/raw /data/exports > /dev/null 2>&1 ||
    true
  compose down > /dev/null 2>&1 || true
  rmdir "$AMPERE_DATA_DIR" || true
}
trap clean_up EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

compose run --rm --no-deps daily ampere daily --help > /dev/null ||
  fail "the daily job has no ampere daily command"
compose run --rm --no-deps daily ampere data init ||
  fail "the daily job cannot write in its data folder"
[[ -f $AMPERE_DATA_DIR/raw/.ampere-raw ]] || fail "the daily job wrote outside its data folder"

compose up --detach --wait --wait-timeout 60 || fail "the container did not become healthy"
compose exec -T api test -f /data/raw/.ampere-raw || fail "the API cannot read the data folder"
if compose exec -T api touch /data/raw/written-by-the-api 2> /dev/null; then
  fail "the API can write in the data folder"
fi
[[ -z $(compose ps --quiet daily) ]] || fail "compose up started the daily job"
status=$(curl --silent --output /dev/null --write-out '%{http_code}' "$base/api/data/recent")
[[ $status == 503 ]] || fail "/api/data/recent answers $status before any export"
compose run --rm --no-deps daily ampere export > /dev/null 2>&1 ||
  fail "the daily job cannot write the export"
curl --fail --silent --show-error "$base/api/data/recent" | grep --quiet '"series":' ||
  fail "the API does not serve the export"
curl --fail --silent --show-error --output /dev/null "$base/api/healthz" ||
  fail "no answer on /api/healthz"
location=$(curl --silent --output /dev/null --write-out '%{redirect_url}' \
  --header 'X-Forwarded-Proto: https' "$base/api/healthz/")
[[ $location == "https://127.0.0.1:$DEMO_PORT/api/healthz" ]] ||
  fail "/api/healthz/ redirects to '$location': uvicorn ignores the proxy's headers"

echo "OK: $DEMO_IMAGE runs with the settings of the online demo"
