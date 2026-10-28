#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
compose=(docker compose --env-file /dev/null -f infra/tests/compose.yml -p finai-e2e)
# Only this disposable project's containers and volumes are removed.
cleanup() {
  exit_status=$?
  trap - EXIT
  "${compose[@]}" down --volumes --remove-orphans >/dev/null || true
  exit "$exit_status"
}
trap cleanup EXIT
if [[ "${1:-}" == "--no-build" ]]; then
  "${compose[@]}" up -d --no-build --wait --wait-timeout 180
else
  "${compose[@]}" up -d --build --wait --wait-timeout 180
fi
"${compose[@]}" exec -T transactions python - < scripts/verify_infrastructure.py
# Real process crash: accept a job while the polling API replica is stopped.
"${compose[@]}" stop transactions
"${compose[@]}" exec -T gateway python - --stage-recovery < scripts/verify_infrastructure.py
"${compose[@]}" start transactions
"${compose[@]}" exec -T gateway python - --check-recovery < scripts/verify_infrastructure.py
# Broker outage cannot discard committed events. Only the test broker is stopped.
"${compose[@]}" stop kafka
"${compose[@]}" exec -T gateway python - --stage-outage < scripts/verify_infrastructure.py
"${compose[@]}" start kafka
"${compose[@]}" exec -T gateway python - --check-outage < scripts/verify_infrastructure.py
(cd web && npm run test:e2e)
