# Architecture

## 1. Context & Goals

This platform demonstrates a production-style pattern for embedding LLMs inside a financial services backend, without treating the LLM as a bolt-on. Goals, in priority order:

1. **Correctness of financial data** — the LLM never directly writes ledger data; it only annotates, summarizes, or advises. All money-affecting writes go through deterministic, testable service code.
2. **Responsiveness under load** — AI calls are slow and rate-limited relative to a DB write, so the system is designed around caching and async/event-driven processing rather than synchronous LLM calls on the hot path wherever avoidable.
3. **Service independence** — each microservice owns its own data and can be deployed, scaled, and tested independently.
4. **Observability & repeatable delivery** — every service ships with health checks, structured logs, and a CI/CD pipeline; nothing is deployed by hand.

**Non-goals:** this is not a payments processor (no money movement/settlement), and it does not aim for PCI-DSS compliance — it's a portfolio demonstration of architecture patterns, not a licensed financial product.

---

## 2. High-Level Design

```
┌─────────────┐        ┌──────────────────────────────────────────────────────┐
│   Client     │        │                   Gateway Service                     │
│ (Web/Mobile) │──HTTP──▶│  - AuthN/AuthZ (JWT)                                  │
└─────────────┘        │  - Request routing                                    │
                        │  - Rate limiting (Redis)                              │
                        └───────┬───────────────┬───────────────┬───────────────┘
                                │               │               │
                    ┌───────────▼──┐   ┌────────▼───────┐   ┌───▼────────────┐
                    │ Transactions  │   │   Assistant     │   │   Insights      │
                    │   Service     │   │    Service      │   │   Service       │
                    │               │   │                 │   │                 │
                    │ - Ingest tx   │   │ - Chat over     │   │ - Scheduled     │
                    │ - AI category │   │   user's data   │   │   summaries     │
                    │ - Anomaly     │   │ - RAG over tx   │   │ - Budget trends │
                    │   flagging    │   │   history       │   │                 │
                    └───┬───────┬───┘   └────────┬────────┘   └────────┬────────┘
                        │       │                │                     │
                        │       │        ┌───────▼────────┐            │
                        │       │        │  LLM Client Lib │            │
                        │       │        │  (OpenAI API,   │◀───────────┘
                        │       │        │  cache, retry)  │
                        │       │        └───────┬────────┘
                        │       │                │
                        │  ┌────▼────┐     ┌─────▼─────┐
                        │  │  Kafka   │     │   Redis    │
                        │  │ (events) │     │  (cache)   │
                        │  └────┬─────┘     └────────────┘
                        │       │
                        │  ┌────▼─────────────┐
                        │  │ Notifications      │
                        │  │ Service (consumer) │
                        │  └───────────────────┘
                        │
                   ┌────▼─────┐
                   │PostgreSQL │
                   │ (system   │
                   │ of record)│
                   └──────────┘
```

---

## 3. Service Boundaries

| Service | Owns | Talks to |
|---|---|---|
| **Gateway** | Auth, routing, rate limiting | All downstream services (HTTP) |
| **Transactions** | Transaction ingestion, categorization, ledger writes | PostgreSQL, LLM client, Kafka (producer) |
| **Assistant** | Conversational Q&A over a user's financial data | PostgreSQL (read), LLM client, Redis (session/cache) |
| **Insights** | Scheduled/aggregate reporting | PostgreSQL (read), LLM client |
| **Notifications** | Alerting on events (large transaction, anomaly, budget threshold) | Kafka (consumer), email/push provider |

**Rule of thumb:** only the **Transactions** service writes financial records. Every other service is read-only against PostgreSQL, which keeps the money-affecting write path small, auditable, and easy to test exhaustively.

---

## 4. Data Flow: "New Transaction" (the core flow)

1. Client (or a simulated bank feed) posts a raw transaction to `Transactions` via the Gateway.
2. `Transactions` writes the raw record to PostgreSQL immediately (status: `pending_categorization`) — the write is never blocked on the LLM.
3. `Transactions` calls the LLM client to classify the transaction (category, merchant normalization, anomaly score).
   - The LLM client checks Redis first for a cached classification of a similar/identical description before calling OpenAI, to cut cost and latency.
4. On classification, the record is updated to `categorized` and a `transaction.categorized` event is published to Kafka.
5. `Notifications` consumes the event; if the anomaly score exceeds a threshold or the amount is unusually large, it sends an alert.
6. `Insights` consumes the same event (or aggregates on a schedule) to keep rolling spending summaries up to date.

This flow keeps the **synchronous request path fast** (a client gets a `202 Accepted` immediately) while the **AI enrichment happens asynchronously** and fans out via events — this is the key pattern this project is meant to showcase.

---

## 5. Key Decisions & Trade-offs

### 5.1 Why event-driven (Kafka) instead of direct service-to-service calls?
- **Decision:** Transaction lifecycle changes are published as events; downstream services subscribe rather than being called synchronously.
- **Why:** Decouples `Transactions` from `Notifications`/`Insights` — either can go down or be redeployed without blocking transaction ingestion. Also gives natural replay/audit capability.
- **Trade-off:** Adds operational complexity (running Kafka/MSK) and eventual consistency — a client polling immediately after posting a transaction may briefly see `pending_categorization`.

### 5.2 Why cache LLM responses in Redis?
- **Decision:** Categorization and common assistant queries are cached by a normalized-input hash.
- **Why:** LLM calls are the slowest and most expensive part of the system; many transaction descriptions repeat (`"STARBUCKS #1234"` appears constantly). Caching cuts both latency and API cost significantly.
- **Trade-off:** Cache invalidation and staleness need care — category corrections by a user should bypass/update the cache, not just the DB row.

### 5.3 Why keep the LLM off the synchronous write path?
- **Decision:** Transaction writes never wait on an LLM call to succeed.
- **Why:** LLM APIs have variable latency and can fail/rate-limit; financial writes must not be blocked by a third-party AI dependency.
- **Trade-off:** Requires a "pending → categorized" state machine and idempotent retry handling instead of a single synchronous write.

### 5.4 Why microservices instead of a modular monolith?
- **Decision:** Split into independently deployable services from the start.
- **Why:** This is a portfolio project meant to demonstrate microservice and Kubernetes patterns explicitly (service boundaries, independent scaling, per-service CI/CD).
- **Trade-off:** More operational overhead than a monolith would need for this scale of traffic — a deliberate trade for demonstrating the pattern, called out here so it's an informed choice, not an oversight.

---

## 6. Reliability & Scaling Notes

- **Idempotency:** transaction ingestion is idempotent on `(account_id, external_tx_id)` to safely handle retries from upstream bank feeds.
- **Backpressure:** the LLM client enforces a token-bucket rate limiter (Redis-backed) shared across service replicas, so horizontal scaling doesn't multiply the effective OpenAI rate limit.
- **Circuit breaking:** if the LLM provider is degraded, categorization falls back to a lightweight rule-based classifier so transactions still get a best-effort category instead of stalling indefinitely.
- **Horizontal scaling:** each service scales independently via K8s HPA on CPU/queue-depth; `Transactions` and `Notifications` are the ones expected to need the most replicas under load.

---

## 7. Security Notes

- JWT-based auth at the Gateway; downstream services trust a signed internal service token, not the end-user token, for service-to-service calls.
- No raw PII/account numbers are sent to the LLM provider — transaction descriptions are normalized/redacted before being included in prompts.
- Secrets (OpenAI API key, DB credentials) are managed via K8s Secrets / AWS Secrets Manager, never committed to the repo.
