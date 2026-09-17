# Build Plan

A phased roadmap for building this project from scratch to a deployed, portfolio-ready state. Each phase has a concrete "done" definition so progress is visible and demoable at every stage — useful both for your own tracking and for showing incremental commits/PRs in the repo history.

---

## Phase 0 — Project Setup (Est. 1–2 days)

**Goal:** a repo that runs locally, even with empty services.

- [x] Initialize repo structure (see `README.md` layout)
- [x] Set up `pyproject.toml` / Poetry (or `uv`) for dependency management
- [x] Write `docker-compose.yml` with Postgres, Redis, Kafka (+ Zookeeper or KRaft), and placeholder service containers
- [x] Add `.env.example` and config loading (pydantic-settings)
- [x] Set up pre-commit hooks: `ruff`, `black`, `mypy`
- [x] Initialize GitHub Actions workflow skeleton (lint + test on push)

**Done when:** `docker compose up` boots all infra containers cleanly and a `/health` endpoint on a stub FastAPI app returns `200`.

**Status: complete.** All five services boot from a shared app factory with `/health`, `/ready` and `/metrics`.

---

## Phase 1 — Core Data Layer (Est. 2–3 days)

**Goal:** the system of record exists and is migration-controlled.

- [x] Design PostgreSQL schema: `users`, `accounts`, `transactions`, `categories`, `audit_log`
- [x] Set up SQLAlchemy models + Alembic migrations
- [x] Seed script with realistic fake transaction data (for demo purposes)
- [x] Write repository-layer unit tests against a test DB (SQLite in the local suite; real Postgres as a service container in CI)

**Done when:** migrations run cleanly from empty DB, seed data loads, and repository tests pass in CI.

**Status: complete.** `alembic upgrade head` builds the schema from empty; `python -m scripts.seed` loads a demo user with ~313 categorized transactions, twice-monthly payroll and one deliberate outlier.

---

## Phase 2 — Transactions Service (Est. 3–5 days)

**Goal:** ingest transactions and write them durably, without AI yet.

- [x] `POST /transactions` — ingest a raw transaction, write `pending_categorization` row
- [x] `GET /transactions/{id}` and `GET /transactions` (paginated, filterable)
- [x] Idempotency handling on `(account_id, external_tx_id)`
- [x] Basic rule-based categorizer as a fallback path (also useful before AI is wired in)
- [x] Unit + integration tests for the ingestion flow

**Done when:** you can POST a batch of transactions and query them back, fully covered by tests, with no AI involved yet.

**Status: complete.** 68 tests pass, ruff/black/mypy clean. Verified against a running server: `202` on ingest, `200` on replay, background categorization, user correction, and `404` (not `403`) when another user asks for the row.

---

## Phase 3 — LLM Integration (Est. 4–6 days)

**Goal:** transactions get AI-categorized asynchronously, with caching and fallback.

- [x] Build shared `llm_client` lib: OpenAI wrapper with retries, timeout, and structured-output parsing
- [x] Redis-backed response cache (normalized description → category)
- [x] Redis-backed rate limiter shared across replicas
- [x] Wire categorization into the transaction flow as an async step (background task or worker)
- [x] Circuit breaker: fall back to rule-based categorizer if OpenAI errors/rate-limits
- [x] Prompt templates versioned in `libs/llm_client/prompts/`, with a small eval script comparing categorization accuracy against a labeled sample set

**Done when:** posting a transaction results in it moving from `pending_categorization` to `categorized` automatically, with cache hit-rate and fallback both demonstrable.

**Status: complete.** Verified against a running server: unknown merchants the rules engine
misses (`SQ *NEIGHBOURHOOD BAKERY`, `TST* THE NOODLE HOUSE`) come back `source=llm`; four
branches of one merchant cost a single provider call (75% cache hit rate); a provider timeout
still yields a categorized transaction via the rules engine; and with Redis down the service
reports `not_ready` but keeps categorizing. Cache hit-rate, call outcomes and categorization
source are exported on `/metrics`. Rules-engine accuracy on the labeled sample is 95%, gated
in CI at 90%.

---

## Phase 4 — Event Bus & Downstream Services (Est. 4–6 days)

**Goal:** Kafka wires the services together; Notifications and Insights come alive.

- [x] Define event schemas (`transaction.categorized`, `transaction.anomaly_flagged`) — pydantic
      models with a `schema_version` envelope; unknown event types and unknown fields are
      skipped rather than failed, so a producer can roll ahead of its consumers. A schema
      registry is the next step if the contract ever needs enforcing at the broker.
- [x] `Transactions` publishes events on categorization
- [x] `Notifications` service: Kafka consumer → alert rules → stub email/push (log-only is fine for portfolio)
- [x] `Insights` service: consumer or scheduled job aggregating spend-by-category, spend-over-time
- [x] `GET /insights/summary` — AI-generated natural-language summary of a period's spending (LLM call over aggregated, not raw, data)

**Done when:** posting a transaction visibly triggers a notification (in logs/UI) and updates a summary endpoint, end to end, across three services.

**Status: complete.** One `POST /transactions` publishes `transaction.categorized`, raises a
`large_transaction` alert readable at `GET /notifications`, and lands in `GET /insights/summary`
— covered end to end in `tests/integration/test_event_fanout.py`. A redelivered event creates no
duplicate alert (dedupe key), and a failing handler holds its offset for redelivery.

**Caveat, stated plainly:** the fan-out test swaps the broker for an in-process publisher, and
the sandbox this was built in could not reach a Kafka distribution, so the aiokafka producer and
consumer have not yet been run against a live broker. Their commit and skip semantics are unit
tested; `docker compose up` is the first place the real broker path executes. Verify it there
before claiming it works.

---

## Phase 5 — Assistant Service (Est. 4–6 days)

**Goal:** conversational Q&A over a user's own data.

- [x] `POST /assistant/chat` — accepts a message + user context, returns an LLM response
- [x] Retrieval step: pull relevant transactions/aggregates from PostgreSQL based on the question before calling the LLM (lightweight RAG, not a vector DB requirement for v1)
- [x] Session/history handling in Redis
- [x] Guardrails: the assistant can *read and explain* financial data but cannot trigger writes (e.g., "move money," "delete transaction" are explicitly out of scope and refused)

**Done when:** you can ask "how much did I spend on dining last month?" and get a correct, grounded answer sourced from real DB rows.

**Status: complete.** Asked against a running service on seeded data: *"You spent $141.12 on
Dining & Coffee in August 2026, across 9 transactions. That's 124% more than the previous
period."* Every figure is computed in SQL and handed to the model as facts plus a correct
draft; the model only rewords. With the provider down the draft is the answer, so accuracy
never degrades. Guardrails are a gate before retrieval, not a prompt instruction: "Move $50 to
my savings account" returns `400 READ_ONLY_ASSISTANT`, while "How much did I transfer to
savings last month?" is answered as the history question it is.

---

## Phase 6 — Gateway, Auth & Hardening (Est. 3–5 days)

**Goal:** a single entry point, secured, rate-limited, and observable.

- [x] JWT auth (login/register or mock SSO) at the Gateway
- [x] Request routing to downstream services
- [x] Rate limiting (Redis token bucket) per user
- [x] Structured logging (JSON logs) + request IDs propagated across services
- [x] Basic metrics (Prometheus-compatible `/metrics`) per service
- [x] Load test (Phase 8 covers full load testing, but do a smoke pass here)

**Done when:** all traffic flows through the Gateway, is authenticated, rate-limited, and traceable via a request ID across service logs.

**Status: complete.** Verified with all five services running: register at the Gateway, then
post and read a transaction, an insights summary and an assistant reply through `/api/v1/*`
with the same JWT. `X-Request-ID: trace-abc-123` appears in both the gateway and insights
logs. Identity is minted at the edge, never forwarded: a client sending `X-User-Id` for
another user is served its own data, and presenting the internal service token at the edge
returns 401. Smoke load: 300 requests at concurrency 25 against a 100/min limit produced
100 OK, 200 shed as 429, zero errors, p95 103ms.

---

## Phase 7 — Containerization & Kubernetes (Est. 3–5 days)  :arrow_left: **in progress**

**Goal:** everything runs on K8s, not just docker-compose.

- [x] Shared multi-stage backend image plus a multi-stage dashboard image
- [x] K8s manifests: Deployments, Services, ConfigMaps, local Secrets, probes, resources, HPA
- [ ] Local K8s testing via `kind` or `minikube`
- [x] Ingress route; production TLS is documented as an overlay concern
- [ ] Helm chart (optional but strong portfolio signal) to templatize manifests across environments

**Done when:** `kubectl apply -f infra/k8s/` brings up the full platform locally in `kind`, with health checks passing and HPA configured.

**Status: in progress.** `kubectl kustomize infra/k8s` renders the full local stack and CI now
gates that render. A live `kind` rollout is still required before this phase is marked complete;
the manifests deliberately do not imply that EKS or AWS infrastructure exists.

---

## Phase 8 — CI/CD, Testing & Cloud Deployment (Est. 4–7 days)

**Goal:** push-to-deploy on AWS, with a real test pyramid behind it.

- [ ] Unit tests (fast, per-service) — target meaningful coverage on business logic, not a vanity percentage
- [ ] Integration tests (service + real Postgres/Redis/Kafka via testcontainers)
- [ ] Load tests (Locust or k6) against the categorization and assistant endpoints — these are the latency-sensitive paths
- [ ] GitHub Actions: lint → test → build image → push to ECR → deploy to EKS
- [ ] Terraform for AWS infra: EKS cluster, RDS (Postgres), ElastiCache (Redis), MSK (Kafka)
- [ ] Rollout strategy: rolling updates with readiness probes; document rollback steps

**Done when:** a merge to `main` triggers a full pipeline that deploys to a live EKS cluster, and you can demo the platform at a public/staging URL.

---

## Phase 9 — Portfolio Polish (Est. 2–3 days)

**Goal:** make it easy for someone else (a recruiter, interviewer) to understand and try in minutes.

- [ ] Record a short demo video/GIF of the core flow (post transaction → see it categorized → ask the assistant about it)
- [ ] Write up 2–3 "engineering decisions" as short blog-style notes (event-driven design, LLM caching, guardrails) — great interview talking points
- [ ] Add architecture diagram image (export from `ARCHITECTURE.md` mermaid/ASCII into a clean visual)
- [ ] Clean up README with badges (build status, license), screenshots, and a live demo link if hosted publicly
- [ ] Tag a `v1.0` release

**Done when:** someone unfamiliar with the project can read the README, understand what it does and why it's architected this way, and try it locally in under 10 minutes.

---

## Suggested Order of Attack

If time is limited, the highest-signal path for a portfolio (demonstrates AI + microservices + events without needing all of Phase 7–8 polished) is:

**Phase 0 → 1 → 2 → 3 → 4 → 5**, then a lightweight version of **6–8** (Docker + basic CI, even without full K8s/Terraform), before investing in **Phase 9** polish. Full K8s/Terraform (Phase 7–8) is the part most worth doing well if the role you're targeting is infra/platform-heavy; trim it first if time is tight and the target role is more backend/AI-focused.
