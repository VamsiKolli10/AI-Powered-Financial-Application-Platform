# AI-Powered Financial Application Platform

> AI-enabled microservices, event-driven processing, and cloud delivery for modern financial workflows.

An AI-powered financial application built on Python and FastAPI microservices, integrating LLM APIs, PostgreSQL, Redis, and Kafka to automate financial workflows and deliver scalable, AI-enabled services.

---

## What This Is

A portfolio-grade backend platform that demonstrates how to combine large language models with traditional financial-services infrastructure. It covers the full lifecycle a real fintech backend needs: ingesting transactions, enriching them with AI, reacting to events in real time, and exposing everything through clean, documented APIs.

**Core capabilities:**
- **AI transaction categorization** — LLM-based classification of raw transaction descriptions into spending categories
- **Conversational financial assistant** — natural-language Q&A over a user's own financial data ("How much did I spend on dining last month?")
- **Anomaly & fraud flagging** — LLM-assisted review of unusual transaction patterns, backed by rule-based pre-filters
- **Automated financial summaries** — scheduled, AI-generated spending/budget reports
- **Event-driven ledger updates** — account balances and downstream services stay in sync via Kafka, not polling

---

## Tech Stack

| Layer | Technology | Purpose |
|---|---|---|
| API layer | **Python 3.12 + FastAPI** | Async request handling, OpenAPI docs out of the box |
| AI/LLM | **OpenAI API** (GPT-4o-class model) | Categorization, chat assistant, summarization |
| Primary datastore | **PostgreSQL** | Accounts, transactions, users, audit trail |
| Cache / session store | **Redis** | LLM response caching, rate limiting, session state |
| Event bus | **Apache Kafka** | Transaction events, notification triggers, async workflows |
| Containerization | **Docker** | Local dev parity, reproducible builds |
| Web client | **React + TypeScript** | Financial overview, transactions, alerts, and assistant |
| Orchestration | **Kubernetes (K8s)** | Local manifests, probes, resource limits, and HPA |
| Cloud target | **AWS** (EKS, RDS, ElastiCache, MSK) | Planned; infrastructure is not implemented yet |
| CI | **GitHub Actions** | Automated backend tests, dashboard build, manifest render, and image builds |

---

## Service Architecture (at a glance)

```
                        ┌────────────────────────────┐
                        │   FastAPI Microservices     │
                        │  LLM-integrated request      │
                        │        handling              │
                        └──────────────┬───────────────┘
                                       │
        ┌──────────────┬──────────────┼──────────────┬──────────────┐
        │              │              │              │              │
   OpenAI APIs     PostgreSQL       Redis        Kafka events   Docker·K8s·AWS
```

See [`ARCHITECTURE.md`](./ARCHITECTURE.md) for the full breakdown of services, data flow, and design decisions.

---

## Repository Structure

```
financial-ai-platform/
├── web/                       # React/TypeScript dashboard served by nginx
├── services/
│   ├── gateway/              # API gateway / BFF, auth, routing
│   ├── transactions/         # Transaction ingestion + AI categorization
│   ├── assistant/            # LLM chat assistant service
│   ├── insights/             # Scheduled summaries & analytics
│   └── notifications/        # Kafka consumer → alerts/emails
├── libs/
│   ├── llm_client/           # Shared OpenAI wrapper (retries, caching, prompt templates)
│   ├── db/                   # SQLAlchemy models, migrations (Alembic)
│   └── events/                # Kafka producer/consumer helpers, schemas
├── infra/
│   ├── docker/                # Dockerfiles, docker-compose.yml
│   ├── k8s/                   # Deployment, service, ingress manifests
│   └── terraform/             # AWS infra as code (EKS, RDS, MSK, ElastiCache)
├── tests/
│   ├── unit/
│   ├── integration/
│   └── load/
├── ARCHITECTURE.md
├── BUILD_PLAN.md
├── API_DESIGN.md
├── .github/workflows/         # CI/CD pipelines
├── docker-compose.yml
├── pyproject.toml
└── README.md
```

---

## Quick Start (Local Dev)

```bash
# 1. Clone and enter the repo
git clone https://github.com/<you>/financial-ai-platform.git
cd financial-ai-platform

# 2. Copy env template and add your OpenAI key
cp .env.example .env

# 3. Spin up Postgres, Redis, Kafka, and all services
docker compose up --build

# 4. Run database migrations and load demo data
docker compose exec transactions alembic upgrade head
docker compose exec transactions python -m scripts.seed

# 5. Confirm it's alive and open the dashboard
curl http://localhost:8000/health
# http://localhost:3000
```

### Without Docker

```bash
uv venv --python 3.12 && source .venv/bin/activate
uv pip install -e ".[dev]"

cp .env.example .env                      # point DATABASE_URL at a local Postgres
alembic upgrade head
python -m scripts.seed
uvicorn services.transactions.main:app --reload --port 8001
```

The seed script creates `demo@example.com` / `demo-password` with two accounts and roughly
six months of transactions, including a deliberate outlier for anomaly detection.

### Common tasks

```bash
make test      # pytest (SQLite-backed, no Docker needed)
make lint      # ruff + black --check + mypy
make format    # ruff --fix + black
make eval      # categorization accuracy against the labeled sample
make smoke     # load smoke test against a running gateway
make up        # docker compose up --build
```

## How the AI path works

Categorization never blocks the durable write. Ingestion returns `202` immediately, and a
background step enriches the row:

```
cache ──hit──────────────────────────────────────────────▶ category
  │miss
  ▼
rate limiter ──refused──▶ rules engine
  │allowed
  ▼
circuit breaker ──open──▶ rules engine
  │closed
  ▼
OpenAI (retries, timeout, JSON-only) ──error/invalid──▶ rules engine
  │
  ▼
validate against the taxonomy ──▶ cache ──▶ category
```

Every branch on the right produces a categorized transaction, so a provider outage costs
accuracy, never availability. Three details worth calling out:

- **The cache is keyed on the merchant, not the description.** `STARBUCKS #1234 SEATTLE WA` and
  `STARBUCKS #9876 PORTLAND OR` collapse to one key, so a merchant is classified once no matter
  how many branches a user visits. Measured on the demo data, four branch locations of one
  merchant cost a single provider call.
- **The rate limiter lives in Redis**, not in the process, so scaling to N replicas does not
  multiply the effective provider rate limit.
- **Descriptions are redacted before they leave the building.** Card fragments, long digit runs,
  store numbers and reference codes are stripped by `normalize_description`; only the merchant
  string and the amount ever reach the provider.

Watch it work on `/metrics`: `llm_cache_events_total`, `llm_calls_total{outcome=...}` and
`categorizations_total{source=...}`.

## Identity at the edge

Clients talk only to the Gateway, at `/api/v1/*`. It verifies the end-user JWT and then
**mints** the downstream identity rather than forwarding anything the client sent:

```
client ──Authorization: Bearer <jwt>──▶ Gateway
                                          │  verify JWT, rate-limit per user
                                          │  strip any client-supplied identity headers
                                          ▼
                    X-Internal-Token + X-User-Id + X-Request-ID
                                          │
                                          ▼
                        transactions · assistant · insights · notifications
```

Downstream services trust the internal token precisely because only the Gateway can produce
it, and the Gateway itself refuses that token — the only credential accepted at the public
edge is a user JWT. So a client that sends `X-User-Id: someone_else` is served its own data,
and one that presents the internal token gets a 401.

Rate limits are per user and per tier: 100 req/min standard, 20 req/min on LLM-backed routes,
in a Redis bucket shared across replicas. If Redis is unreachable the limiter degrades to a
per-replica bucket rather than failing open — a cache outage should cost the *accuracy* of the
limit, not the limit itself.

## How the assistant stays honest

Ask *"How much did I spend on dining last month?"* and the answer is built like this:

1. **Guardrail first.** Write intent is refused before any retrieval or model call — a gate in
   code, not an instruction in a prompt. `Move $50 to savings` gets `400 READ_ONLY_ASSISTANT`;
   `How much did I transfer to savings last month?` is answered, because it is a question about
   history.
2. **Retrieve.** The question is parsed into a period and category, and the figures come from a
   SQL aggregate scoped to the asking user's own accounts. No vector database: the questions are
   about a structured ledger, so the right retrieval is a scoped aggregate, not a similarity search.
3. **Draft in Python.** A correct sentence is composed from those figures. All arithmetic happens
   here.
4. **Reword with the model.** The model receives the facts and the draft, and is told to improve
   the wording and invent nothing. If it fails, is rate limited, or returns junk, the draft is
   what the user sees.

So the assistant degrades in style, never in accuracy. The response carries `reply_source`
(`llm` or `draft`) so callers can tell which they got — the numbers are identical either way.

## What one transaction sets off

```
POST /transactions ──▶ row written (pending_categorization) ──▶ 202 Accepted
                                    │
                          (background, after commit)
                                    ▼
                        category + anomaly score
                                    │
                       publish to Kafka: transactions.events
                          ├── transaction.categorized
                          └── transaction.anomaly_flagged   (score ≥ threshold)
                                    │
              ┌─────────────────────┴─────────────────────┐
              ▼                                           ▼
      Notifications consumer                      Insights (/summary)
      alert rules → stored alert                  aggregates from Postgres,
      → GET /notifications                        AI sentence on top
```

Events are published only after the session commits, so no event can describe a row that
was rolled back. The inverse — committed but unpublished, if the broker is down — is the
trade-off taken; a transactional outbox is the natural next step if delivery ever has to be
guaranteed. Consumers are idempotent (alerts carry a dedupe key) because Kafka delivers at
least once, and a handler that raises does not commit its offset, so the message is
redelivered rather than lost.

Set `LLM_ENABLED=false` (or leave `OPENAI_API_KEY` empty) and the whole platform runs on the
deterministic rules engine — that mode is a first-class citizen, not a broken state.

Interactive API docs (Swagger UI) will be available at `http://localhost:8000/docs` once the gateway is up.

## React dashboard

The dashboard at `http://localhost:3000` is a real client of the Gateway, not a static mockup.
After signing in with the seeded demo user, it derives cash-flow totals from recent transactions,
renders the monthly spending summary and category breakdown, surfaces anomaly notifications, and
sends grounded questions to the read-only financial assistant. nginx keeps browser traffic
same-origin and proxies `/api/*` to the Gateway.

For frontend-only development:

```bash
cd web
npm ci
npm run dev
```

---

## Implementation Highlights

- Developed Python and FastAPI microservices that integrate LLM APIs with PostgreSQL, Redis, and Kafka to automate financial workflows end to end.
- Implemented backend API integration, event-driven processing, and caching to keep AI-enabled services responsive under load.
- Covered delivery with automated testing, containerization, and cloud deployment for reliable, repeatable releases.

---

## Documentation

| Doc | Purpose |
|---|---|
| [`ARCHITECTURE.md`](./ARCHITECTURE.md) | System design, service boundaries, data flow, key decisions |
| [`BUILD_PLAN.md`](./BUILD_PLAN.md) | Phased roadmap for building this out, milestone by milestone |
| [`API_DESIGN.md`](./API_DESIGN.md) | Endpoint reference, auth, request/response contracts |

---

## Status

🚧 Portfolio project — actively being built.

| Phase | State |
|---|---|
| 0 — Project setup, Docker Compose, CI | ✅ Complete |
| 1 — Core data layer (models, migrations, seed) | ✅ Complete |
| 2 — Transactions service (ingest, idempotency, rules categorizer) | ✅ Complete |
| 3 — LLM integration (caching, rate limiting, circuit breaker) | ✅ Complete |
| 4 — Kafka event bus, Notifications + Insights | ✅ Complete |
| 5 — Assistant service (conversational Q&A) | ✅ Complete |
| 6 — Gateway, auth, rate limiting, observability | ✅ Complete |
| 7 — Kubernetes manifests and local `kind` deploy | 🟡 Manifests implemented; live `kind` verification pending |
| 8 — CI/CD and cloud deployment | 🟡 CI implemented; AWS deployment planned |
| 9 — Portfolio polish | 🟡 React dashboard implemented; media/live demo planned |

## License

Licensed under the [MIT License](LICENSE).

## Contributing and security

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup and checks, and
[SECURITY.md](SECURITY.md) for private vulnerability reporting.
