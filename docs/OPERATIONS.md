# Operations Runbook

How to run, observe, and troubleshoot the platform. For configuration see
[`CONFIGURATION.md`](CONFIGURATION.md); for AWS provisioning and rollback see
[`infra/terraform/aws/README.md`](../infra/terraform/aws/README.md); for the local Kubernetes
deployment see [`infra/k8s/README.md`](../infra/k8s/README.md).

## Service map

| Component | Compose port | In-cluster address | Readiness depends on |
|---|---|---|---|
| Database initialization (one-shot) | — | `database-init` | PostgreSQL; applies migrations and creates an empty local workspace |
| Dashboard (nginx) | 3000 | `dashboard:80` | — (`/healthz`) |
| Gateway | 8000 | `gateway:8000` | PostgreSQL |
| Transactions | 8001 | `transactions:8000` | PostgreSQL; Redis when `LLM_ENABLED=true` |
| Assistant | 8002 | `assistant:8000` | PostgreSQL; Redis when `LLM_ENABLED=true` |
| Insights | 8003 | `insights:8000` | PostgreSQL |
| Notifications | 8004 | `notifications:8000` | PostgreSQL |
| PostgreSQL 16 | 5432 | `postgres:5432` | |
| Redis 7 | 6379 | `redis:6379` | |
| Kafka 3.9 (KRaft) | 9092 | `kafka:9092` | |

Kafka is not part of any readiness check, but with `KAFKA_ENABLED=true` Transactions and
Notifications need it reachable **at startup**.
In local Compose, all API services wait for `database-init` to exit successfully; the
dashboard then waits for the Gateway health check. This ensures the demo credentials are
usable before the login page becomes available on a fresh database.

## Start, stop, migrate, seed

```bash
make up                                                   # migrations + seed, then services
docker compose ps -a                                       # database-init should exit 0
docker compose logs database-init                          # setup output or failure
docker compose exec transactions python -m scripts.seed   # optional repeat; existing demo unchanged
make logs                                                 # tail the five services
make down                                                 # stop (volumes kept)
docker compose down -v                                    # stop and delete all data
```

Startup creates the local login `demo@example.com` / `demo-password` without sample activity.
Sample data is opt-in with `python -m scripts.seed --demo-transactions`; its options are
`--months` (6), `--transactions` (400), and `--seed` (42). An existing nonempty ledger is
left alone. `python -m scripts.seed --clear-demo-transactions` removes only generator-tagged
rows owned by the starter login, their related alerts, and unused sample credit accounts;
it reverses their balance changes and preserves other users and manually added transactions.
Cleanup is permitted only in local/test environments. Use `docker compose exec transactions`
before these commands when running in Compose.

## Health checks

```bash
for p in 8000 8001 8002 8003 8004; do curl -s localhost:$p/ready; echo; done

# The same picture through the Gateway, as the dashboard sees it (needs a user token):
curl -s localhost:8000/api/v1/status -H "Authorization: Bearer $TOKEN" | jq
```

- `GET /health` → `200` whenever the process is up (liveness).
- `GET /ready` → `200 {"status":"ready","checks":{"database":"ok",...}}`, or `503
  {"status":"not_ready","checks":{"redis":"error: ..."}}` naming the failing dependency.

Kubernetes uses `/health` for liveness and `/ready` for readiness, so a pod whose dependency is
down is taken out of rotation rather than restarted.

The dashboard's status card calls `GET /api/v1/status` every 60 s while the tab is visible. The
Gateway probes each service's `/ready` (2 s timeout, cached 5 s) and returns only `ok` or
`unavailable` per service; look for `status_probe_failed` / `status_probe_not_ready` in the
Gateway logs for the reason. If the status endpoint itself fails, the card falls back to
whether the page's own data requests succeeded and says so.

## Logs and tracing

Logs are one JSON object per line. Every request is logged with `method`, `path`, `status` and
`duration_ms` (except `/health`, `/ready`, `/metrics`), tagged with the request id.

To follow one request across services, send your own id and grep for it:

```bash
curl -H "X-Request-ID: trace-abc-123" -H "Authorization: Bearer $TOKEN" localhost:8000/api/v1/insights/summary
docker compose logs gateway insights | grep trace-abc-123
```

Useful log events: `transaction_ingested`, `transaction_categorized`,
`categorization_failed_using_fallback`, `event_published`, `event_publish_failed`,
`alert_delivered`, `alert_suppressed_duplicate`, `rate_limited`,
`rate_limiter_degraded_to_local_bucket`, `breaker_open`, `upstream_unreachable`,
`assistant_request_refused`, `event_handler_failed`, `consumer_message_error`,
`consumer_loop_error`, `event_invalid_skipped`, `event_undecodable_skipped`.

The Notifications consumer retries a failed handler or offset commit after 1 s, holding the
same record before reading another. Commits acknowledge only that record's partition and next
offset. Fetch errors also retry after 1 s. If a partition is revoked during a retry, its new
owner resumes from the committed offset. Handlers must remain idempotent: a successful handler
can run again if its offset commit fails. Invalid JSON, non-object payloads, invalid known
events and unknown event types are logged, committed and skipped; raw validation payloads are
not logged. There is no dead-letter queue yet.
Revocation is tracked through aiokafka's rebalance callbacks rather than `assignment()` alone.
The loop abandons held work on revocation and resumes polling so the consumer can rejoin after
the poll timeout; an old assignment epoch cannot commit.

Notifications with `delivered_at IS NULL` have pending channel delivery. Event replay retries
them after a channel failure or restart; the `read` flag does not affect delivery. A crash after
send but before the delivery timestamp commits can deliver twice. An external channel must use
the alert's stable dedupe key if it needs to suppress these duplicates.

## Metrics

Every service exposes Prometheus text at `/metrics`.

| Metric | Type | Labels | Read it as |
|---|---|---|---|
| `http_requests_total` | counter | `service`, `method`, `path`, `status` | Traffic and error rate per route |
| `http_request_duration_seconds` | histogram | `service`, `method`, `path` | Latency per route |
| `llm_calls_total` | counter | `outcome` | Provider calls by result (success, error, rate limited, …) |
| `llm_call_duration_seconds` | histogram | | Provider latency |
| `llm_cache_events_total` | counter | `result` | Cache hit/miss — hit rate = hits / (hits + misses) |
| `categorizations_total` | counter | `source` | How many categorizations came from the LLM vs. the rules engine |

A rising share of `categorizations_total{source="rules"}` while the LLM is enabled means the
provider is failing, rate limited, or the circuit is open.

## Degraded modes

Each dependency failure is designed to cost something specific, not everything.

| Failure | Effect | User-visible? |
|---|---|---|
| OpenAI down / slow / invalid output | Categorization falls back to rules; assistant returns its draft; summary uses the template. Breaker opens after 5 consecutive failures, probes again after 30 s. | Only via `category_source`, `reply_source`, `summary_source`. Figures are unchanged. |
| LLM rate limit reached | Same fallbacks as above until tokens refill. | As above. |
| Redis down | Gateway rate limiting degrades to a per-replica bucket (limit × replicas). LLM cache misses; LLM limiter fails open. Chat history falls back to per-process memory. Transactions/Assistant report `not_ready` when `LLM_ENABLED=true`. | Follow-up questions may lose context across replicas. |
| Kafka down after startup | Rows commit and are categorized; `event_publish_failed` is logged and that event is lost. No alerts for those transactions. Insights is unaffected. | Missing notifications. |
| Kafka down at startup | Transactions and Notifications fail to start (container restarts). | Ingestion unavailable until the broker is reachable. |
| PostgreSQL down | Every service reports `not_ready`; requests fail with `500`. | Yes. |
| A downstream service down | Gateway returns `502 UPSTREAM_ERROR` for that service only. | For that feature only. |

## Troubleshooting

**Demo login returns 401.** Login intentionally uses the same message for a missing account
and a wrong password. The local Compose initialization job should create `demo@example.com`
with password `demo-password`. Check `docker compose ps -a` and
`docker compose logs database-init`; fix any setup failure and retry
`docker compose up -d --build`. For an older stack without this job, run the seed command
above (also creates the current demo account if an older seed used `demo@finai.local`).
Seeding preserves an existing account's password, so it will not undo an intentional password
change. The previous startup flow could serve a healthy login page before the separate manual
seed command ran; the initialization dependency closes that gap.

**Transaction stays `pending_categorization`.** Inspect its `categorization_jobs` row: attempts,
available time, lease expiry and error type. Another polling worker recovers an expired lease.
Provider failures normally use rules; database or event-construction failures retry with delays
up to 60 seconds. After 10 attempts the row is parked. Fix the underlying cause, then reset
attempts to zero, clear `locked_until` / `lock_token`, and set `available_at` to the current time
for the specific failed job. Do not modify a live lease. Logs use `durable_work_retry` and
`worker_step_failed`; provider error bodies are not stored in the job.

**Outbox is not advancing.** Check broker readiness and `outbox_events.last_error`, attempts
and lease expiry. A pending or parked event blocks later events for its account to preserve
claim order. Restore the broker first; reset a parked row only after fixing its cause. Keep
the row's ID and payload unchanged so any redelivery retains its identity. Completed rows
are retained for inspection; scheduled retention/archival remains future operational work.

**No notifications appear.** Check, in order: `KAFKA_ENABLED=true` on both Transactions and
Notifications; `event_published` in Transactions logs; `kafka_consumer_started` in Notifications
logs; the amount actually crosses a threshold (deposits never raise `large_transaction`).
Duplicates are suppressed by design (`alert_suppressed_duplicate`).

**Notifications stop advancing with `event_handler_failed`.** The consumer holds the failed
record and retries rather than acknowledging it with a later event. A persistent handler error
delays all partitions assigned to that consumer; restore the failing dependency or fix the
handler. `consumer_message_error` indicates an offset commit or other record-processing error;
`consumer_loop_error` indicates a fetch failure. Malformed events are skipped with
`event_invalid_skipped` / `event_undecodable_skipped` and do not stop the loop.

**`401` when calling the Gateway with `X-Internal-Token`.** Expected — the Gateway accepts only
user JWTs. Call downstream services directly (ports 8001–8004) if you need the internal token.

**`401 Expected a access token.`** A refresh token was sent as the bearer token. Use
`access_token`, or exchange the refresh token at `/api/v1/auth/refresh`.

**`429 RATE_LIMITED`.** Wait for `Retry-After` seconds or raise `RATE_LIMIT_STANDARD` /
`RATE_LIMIT_LLM` for local testing.

**Swagger at `:8000/docs` shows only auth routes.** By design; open each service's own `/docs`
on ports 8001–8004.

**Services fail at startup with a `ValueError` about secrets.** `ENVIRONMENT` is `staging` or
`production` with development defaults. See [Startup validation](CONFIGURATION.md#startup-validation).

## Load smoke test

```bash
make smoke    # 200 requests, concurrency 20, against http://localhost:8000
python -m scripts.smoke_load --requests 300 --concurrency 25 --max-p95-ms 250
```

The script registers `smoke@example.com` (or logs in if it exists), then hammers
`/api/v1/transactions`. Expect 429s once the per-user limit is exhausted; that is the limiter
working. `--max-p95-ms` makes it exit non-zero above a latency budget.

## Releases and rollback

- **Kubernetes:** images are tagged by commit. `kubectl -n financial-ai rollout undo
  deployment/<name>` reverts an in-progress or bad rollout; readiness probes keep unready pods out
  of service.
- **AWS:** `SKIP_BUILD=true IMAGE_TAG=<previous sha> ./scripts/deploy-aws.sh` redeploys a known
  good release. Details in the Terraform README.
- **Database:** migrations are forward-only in practice. Run `alembic upgrade head` before
  rolling out code that depends on a new migration (the Kubernetes `database-migrations` Job and
  the AWS deploy script both do this).

Migration `20261002063931_notification_delivery.py` (internal revision `0003`) adds
notification delivery tracking and rewrites legacy budget keys to include
user and transaction currency (USD when the referenced transaction is missing). Historical
alerts are marked delivered using `created_at` to prevent replay from resending them. This does
not recover historical alerts or deliveries already lost before tracking existed. Stop the
Notifications consumer, run the migration, then start the updated service; do not run old and
new budget-key writers together. PostgreSQL migration SQL can be rendered with
`alembic upgrade head --sql` without connecting to a database.

Migration `20261028160000_seed_canonical_categories.py` initializes the 17 canonical categories
on a fresh database and fills missing categories on existing installations. It preserves existing
category metadata and creates no users, accounts, or transactions. Its downgrade retains reference
rows so rollback does not clear categories from financial records. Apply it with
`alembic upgrade head`; the local demo seed is optional for Kubernetes and AWS installations.

## Known issues

- Normal unit tests use SQLite and in-process event doubles. Run
  `bash scripts/test_application.sh` for isolated PostgreSQL/Redis/Kafka and browser tests,
  including process restart, broker outage and duplicate replay. This runner uses its own
  containers and volumes, never the local development database or private `.env`.
- Exactly-once channel delivery is not guaranteed. External channels need their own
  idempotency support. The current channel logs alerts; it does not send email or SMS.
- The numerical rewrite validator is conservative and cannot prove that arbitrary model prose
  is semantically equivalent. More comprehensive semantic evaluation remains future work.
