# API Design

All endpoints are exposed through the **Gateway** at `/api/v1/...`. Downstream service routes shown here reflect internal structure; the Gateway proxies to them.

## Authentication

All routes except `/auth/*` and `/health` require a Bearer JWT.

```
Authorization: Bearer <token>
```

| Endpoint | Method | Description |
|---|---|---|
| `/api/v1/auth/register` | POST | Create a user account |
| `/api/v1/auth/login` | POST | Exchange credentials for a JWT |
| `/api/v1/auth/refresh` | POST | Refresh an expiring token |

**Error format (all services):**
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "amount must be greater than 0",
    "request_id": "a1b2c3d4"
  }
}
```

**Rate limits:** 100 req/min per user on standard endpoints, 20 req/min on `/assistant/chat` and any LLM-backed endpoint (returns `429` with `Retry-After` header when exceeded).

---

## Transactions Service

### `POST /api/v1/transactions`
Ingest a new transaction. Returns immediately; AI categorization happens asynchronously.

**Request:**
```json
{
  "account_id": "acct_123",
  "external_tx_id": "bank_tx_98765",
  "amount": -42.50,
  "currency": "USD",
  "description": "STARBUCKS #1234 SEATTLE WA",
  "occurred_at": "2026-09-05T14:30:00Z"
}
```

**Response `202 Accepted`:**
```json
{
  "id": "tx_abc123",
  "status": "pending_categorization",
  "amount": -42.50,
  "description": "STARBUCKS #1234 SEATTLE WA",
  "occurred_at": "2026-09-05T14:30:00Z"
}
```

### `GET /api/v1/transactions/{id}`
Returns full transaction detail including category once available.

```json
{
  "id": "tx_abc123",
  "status": "categorized",
  "amount": -42.50,
  "currency": "USD",
  "description": "STARBUCKS #1234 SEATTLE WA",
  "category": "Dining & Coffee",
  "anomaly_score": 0.02,
  "occurred_at": "2026-09-05T14:30:00Z",
  "categorized_at": "2026-09-05T14:30:04Z"
}
```

### `GET /api/v1/transactions`
Paginated, filterable list.

**Query params:** `account_id`, `category`, `status`, `from`, `to`, `cursor`, `limit` (default 25, max 100)

### `PATCH /api/v1/transactions/{id}`
User correction of an AI-assigned category (also updates the LLM cache entry for that normalized description).

```json
{ "category": "Groceries" }
```

---

## Assistant Service

### `POST /api/v1/assistant/chat`
Conversational Q&A grounded in the user's own transaction data.

**Request:**
```json
{
  "conversation_id": "conv_555",
  "message": "How much did I spend on dining last month?"
}
```

**Response:**
```json
{
  "conversation_id": "conv_555",
  "reply": "You spent $312.40 on Dining & Coffee in August, across 18 transactions — about 22% more than your 3-month average.",
  "sources": [
    { "type": "aggregate", "category": "Dining & Coffee", "period": "2026-08" }
  ]
}
```

**Guardrail behavior:** requests that imply a write action (e.g., "move $50 to savings," "delete my last transaction") are refused with a `400` and an explanation that the assistant is read-only; the response includes guidance to use the relevant write endpoint directly.

### `GET /api/v1/assistant/conversations/{id}`
Retrieve conversation history (stored in Redis with TTL, or persisted to Postgres if `persist=true` was set at creation).

---

## Insights Service

### `GET /api/v1/insights/summary`
AI-generated natural-language summary over a period, built from pre-aggregated data (never raw LLM access to full transaction dumps).

**Query params:** `account_id`, `period` (`weekly` | `monthly`), `from`, `to`

```json
{
  "period": "2026-08",
  "total_spend": 2140.55,
  "by_category": [
    { "category": "Dining & Coffee", "amount": 312.40 },
    { "category": "Groceries", "amount": 480.10 }
  ],
  "summary": "August spending was up 8% vs. July, driven mainly by dining and one large one-off purchase in Electronics."
}
```

### `GET /api/v1/insights/trends`
Structured (non-LLM) time series data for charting — category spend over the last N periods.

---

## Notifications Service

Notifications are event-driven and not directly invoked via API by clients, but expose a read endpoint:

### `GET /api/v1/notifications`
List recent alerts for the authenticated user (large transaction, anomaly flagged, budget threshold crossed).

```json
{
  "notifications": [
    {
      "id": "notif_1",
      "type": "anomaly_flagged",
      "transaction_id": "tx_abc123",
      "message": "Unusual transaction: $842.00 at an unfamiliar merchant.",
      "created_at": "2026-09-05T14:30:10Z",
      "read": false
    }
  ]
}
```

### `PATCH /api/v1/notifications/{id}`
Mark as read: `{ "read": true }`

---

## Internal Event Schemas (Kafka)

Not part of the public API, but documented here since they're the contract between services.

### `transaction.categorized`
```json
{
  "event_type": "transaction.categorized",
  "transaction_id": "tx_abc123",
  "account_id": "acct_123",
  "category": "Dining & Coffee",
  "anomaly_score": 0.02,
  "occurred_at": "2026-09-05T14:30:00Z",
  "published_at": "2026-09-05T14:30:04Z"
}
```

### `transaction.anomaly_flagged`
```json
{
  "event_type": "transaction.anomaly_flagged",
  "transaction_id": "tx_abc123",
  "account_id": "acct_123",
  "anomaly_score": 0.91,
  "reason": "amount_outlier",
  "published_at": "2026-09-05T14:30:04Z"
}
```

---

## Health & Observability (every service)

| Endpoint | Purpose |
|---|---|
| `GET /health` | Liveness — is the process up |
| `GET /ready` | Readiness — can it serve traffic (DB/Kafka/Redis reachable) |
| `GET /metrics` | Prometheus-format metrics |
