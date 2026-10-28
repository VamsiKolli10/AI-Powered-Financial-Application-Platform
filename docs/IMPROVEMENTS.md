# Six improvements — implementation and verification

Requested scope (2026-10-07): complete all six improvements identified in the portfolio review.
Existing local changes and private provider configuration are preserved.

| Requirement | Completion evidence required | Status |
| --- | --- | --- |
| Validate AI output | Reject changed/invented figures, wrong currencies and malformed summaries; retain valid responses and deterministic fallbacks; regression and live checks | Complete: `test_grounding.py`, assistant/insights regressions, live `verify_provider --live` accepted both model responses and cache reuse |
| Complete dashboard workflow | Account selection, manual entry and CSV import, category corrections and alert read actions; validation, ownership, refresh and browser tests | Complete: API ownership/atomicity regressions, component retry/error tests, real browser workflow passed |
| Durable categorization | Transaction and job commit together; independent workers, bounded attempts, expired lease recovery, no stale writes; restart/concurrency tests | Complete: durable work regressions, real PostgreSQL concurrent ingestion and claims, API shutdown/restart recovery passed |
| Transactional outbox | Categorization and events commit together; acknowledged publishing, retries, stable identity, per-account ordering and duplicate-safe consumers; broker outage tests | Complete: rollback/replay regressions, real PostgreSQL account FIFO, Kafka outage recovery and duplicate replay passed |
| Repeatable application tests | Checked-in browser tests and isolated PostgreSQL/Redis/Kafka integration runner; normal CI needs no provider key, live provider checks opt in | Complete: `test_application.sh --no-build` passed using freshly built locked images; runner, Playwright workflow and CI job checked in |
| Measure AI quality | Separate held-out evaluation, rules/AI comparison, per-category accuracy, latency and fallback frequency; reproducible report and optional live run | Complete: frozen 34-example set, `eval_quality.py`, checked-in rules/live JSON and evaluation notes; live 33/34, rules 4/34, no provider fallbacks |

Do not mark complete using only unit tests: the durable queues require PostgreSQL
concurrency and recovery evidence, and the dashboard requires real browser flows.
Do not print environment files, credentials, access tokens, or provider error bodies.

## Verification (2026-10-07 Pacific / 2026-10-08 UTC)

- Full backend suite: 362 passed. The subsequently added bounded-attempt regression and
  durable-job migration were verified in a 10-test focused run, all passing.
- Dashboard: 45 unit/component tests passed; TypeScript/Vite production build passed.
- Isolated runner: PostgreSQL ingestion/idempotency concurrency, exclusive claims, expired
  lease fencing, fresh-worker recovery, account FIFO, Redis cache and rate limiting passed.
- A stopped transaction API left committed work recoverable; a restarted API delivered it
  through Kafka to Notifications. Stopping the test broker retained pending outbox events;
  restarting it delivered them. Replaying acknowledged events preserved alert deduplication.
- Checked-in browser test passed: empty user workspace, account selection, manual entry,
  category correction, CSV import and replay, grounded chat, mark alert read, invalid import,
  cross-user correction rejection.
- Live OpenRouter checks accepted validated assistant and summary responses and reused the
  summary cache. The separate 34-example evaluation measured per-category results,
  classification latency and fallbacks; see `docs/evaluations/README.md`.
- Ruff, Black, mypy, whitespace checks and both Kubernetes manifest renders passed.

The isolated stack was removed by the runner. Existing local development data was untouched.
No production deployment, Git commit or external notification was performed.

## Explicit limits

The rewrite validator checks ordered numerical/identity anchors, not full semantic equivalence.
At-least-once delivery can replay events after acknowledgement; delayed duplicates do not provide
an exactly-once ordering guarantee. After ten attempts, poison work is parked and requires
operator investigation; completed-work archival is future maintenance. Notification delivery
currently logs locally. Evaluation labels are synthetic and provisional, with only two cases
per category. CI configuration was added locally; hosted GitHub CI has not been run here.
