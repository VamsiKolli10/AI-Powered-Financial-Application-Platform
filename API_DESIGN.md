# API Reference

Clients call only the **Gateway**, under `http://<host>:8000/api/v1`. The Gateway verifies the
user's JWT, applies rate limits, and forwards `/api/v1/{service}/...` to the matching downstream
service at `/{service}/...`. Downstream paths below are written relative to `/api/v1`.

> **Interactive docs.** Each service serves Swagger UI at `/docs` and its schema at
> `/openapi.json`. The Gateway's `/docs` (port 8000) lists only the `/auth` routes because the
> proxy routes are excluded from its schema; for the full request/response models, open the
> service's own docs locally — transactions `:8001/docs`, assistant `:8002/docs`, insights
> `:8003/docs`, notifications `:8004/docs`.

## Conventions

| Topic | Behavior |
|---|---|
| Auth | `Authorization: Bearer <access_token>` on every route except `register`, `login`, `refresh` and the ops endpoints. |
| IDs | Prefixed and roughly time-sortable: `usr_…`, `acct_…`, `tx_…`, `notif_…`, `conv_…`. |
| Money | Decimal values serialized as JSON strings with two places (`"-42.50"`). Negative = outflow, positive = inflow. |
| Timestamps | ISO 8601. Send timezone-aware values (`2026-09-05T14:30:00Z`). |
| Request ID | Send `X-Request-ID` to trace a call; otherwise one is generated. It is returned on the response and forwarded to downstream services and their logs. |
| Ownership | A resource that belongs to another user returns `404`, never `403`, so its existence is not confirmed. |

### Error envelope

Every error from every service has the same shape:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "amount: Value error, amount must not be zero",
    "request_id": "a1b2c3d4e5f6"
  }
}
```

| Status | `code` | When |
|---|---|---|
| 400 | `READ_ONLY_ASSISTANT` | The assistant was asked to perform a write (see [Assistant](#assistant)). |
| 401 | `UNAUTHORIZED` | Missing, malformed, expired or wrong-type token; bad credentials; internal token presented at the Gateway. |
| 404 | `NOT_FOUND` | Unknown resource, a resource owned by someone else, or an unknown service prefix. |
| 409 | `CONFLICT` | Registering an email that already exists. |
| 422 | `VALIDATION_ERROR` | Request body or query failed validation. The message names the first failing field. |
| 429 | `RATE_LIMITED` | Rate limit exceeded. Includes a `Retry-After` header (seconds). |
| 502 | `UPSTREAM_ERROR` | The Gateway could not reach a downstream service or it timed out (15 s). |
| 500 | `INTERNAL_ERROR` | Unhandled error. Details are logged, not returned. |

### Rate limits

Per user, per minute, enforced at the Gateway with a token bucket shared in Redis.

| Tier | Default | Applies to | Setting |
|---|---|---|---|
| LLM | 20 req/min | `/assistant/*`, `/insights/summary` | `RATE_LIMIT_LLM` |
| Standard | 100 req/min | everything else that is proxied | `RATE_LIMIT_STANDARD` |

`/auth/*` routes are not rate limited. If Redis is unreachable the Gateway falls back to a
per-replica bucket, so the effective limit becomes `limit × replicas` rather than unlimited.

---

## Auth

### `POST /auth/register` → `201 Created`

Creates a user plus a starter account named "Everyday Checking", and returns tokens.

```json
{ "email": "ada@example.com", "password": "at-least-8-chars", "full_name": "Ada Lovelace" }
```

`password` is 8–72 characters (bcrypt's 72-byte limit is enforced, not silently truncated).
`full_name` is optional. Emails on reserved domains such as `.local` fail validation.

### `POST /auth/login` → `200 OK`

```json
{ "email": "demo@example.com", "password": "demo-password" }
```

Wrong email and wrong password return the same `401 Incorrect email or password.`

**Token response** (register, login, refresh):

```json
{
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "token_type": "bearer",
  "expires_in": 3600
}
```

Access tokens last `JWT_EXPIRE_MINUTES` (default 60). Refresh tokens last 30 days. Tokens carry a
`type` claim, so a refresh token is rejected where an access token is expected and vice versa.

### `POST /auth/refresh` → `200 OK`

```json
{ "refresh_token": "eyJ..." }
```

Returns a new access and refresh token pair. Fails with `401` if the user no longer exists or is
disabled. Refresh tokens are not revoked server-side; there is no logout endpoint.

### `GET /auth/me` → `200 OK`

```json
{ "id": "usr_m1a2b3c4d5e6f7", "email": "demo@example.com", "full_name": "Demo User" }
```

### `GET /status` → `200 OK`

Readiness of every service, for status displays. The Gateway calls each downstream
service's `/ready` (2 s timeout) plus its own database check, and caches the result for 5
seconds per replica. Requires a user token; counts against the standard rate limit.

```json
{
  "status": "degraded",
  "services": {
    "gateway": "ok",
    "transactions": "ok",
    "assistant": "ok",
    "insights": "unavailable",
    "notifications": "ok"
  },
  "checked_at": "2026-10-01T21:30:00Z"
}
```

`status` is `ok` only when every service is `ok`. A service is `unavailable` when its
readiness probe answers non-200 or cannot be reached. Probe details (hostnames, exception
text) are logged, never returned.

---

## Transactions

`GET /transactions/accounts` returns only the caller's accounts (`id`, `name`, `currency`).
`GET /transactions/categories` returns the canonical slug-to-name map for category editing.
Both require authentication. These static routes precede the transaction-ID route.

`POST /transactions/import` accepts JSON `{ "account_id": "...", "csv_text": "..." }`.
The UTF-8 CSV is limited to 256 KiB and 500 rows. Required headers are
`occurred_at,description,amount`; optional headers are `currency,external_tx_id`.
Date-only values mean midnight UTC; timestamps require a timezone. The dashboard displays
ledger dates in UTC, matching reporting periods and the manual entry's Date (UTC) field.
Amounts must be nonzero,
finite decimals with at most two decimal places. Blank descriptions, malformed rows and
duplicate external IDs within a file return `422`. Ownership is checked before parsing.
All rows validate before any writes; the batch's transactions, balances, audit entries and
categorization jobs commit atomically. `202` returns `created`, `skipped` and `transaction_ids`.
Reimporting identical content is idempotent. A supplied bank external ID also deduplicates
across different files; otherwise the ID derives from the file content and row number.

### `POST /transactions` → `202 Accepted` (new) or `200 OK` (replay)

Writes the transaction durably and returns immediately. Categorization and anomaly scoring run in
the background after the write commits. Ingestion is idempotent on
`(account_id, external_tx_id)`: re-sending the same pair returns the existing row with `200`.

```json
{
  "account_id": "acct_123",
  "external_tx_id": "bank_tx_98765",
  "amount": "-42.50",
  "currency": "USD",
  "description": "STARBUCKS #1234 SEATTLE WA",
  "occurred_at": "2026-09-05T14:30:00Z"
}
```

| Field | Rules |
|---|---|
| `account_id` | Must be an account owned by the caller, otherwise `404`. |
| `external_tx_id` | Up to 120 chars. The upstream feed's id; the idempotency key. |
| `amount` | Non-zero. Rounded to cents. |
| `currency` | 3 letters, upper-cased. Default `USD`. |
| `description` | 1–500 chars. Stored as sent; only a redacted form is ever sent to the LLM. |

The account balance is adjusted in the same database transaction as the insert.

```json
{
  "id": "tx_m1a2b3c4d5e6f7",
  "status": "pending_categorization",
  "amount": "-42.50",
  "description": "STARBUCKS #1234 SEATTLE WA",
  "occurred_at": "2026-09-05T14:30:00Z"
}
```

### `GET /transactions/{id}` → `200 OK`

```json
{
  "id": "tx_m1a2b3c4d5e6f7",
  "account_id": "acct_123",
  "external_tx_id": "bank_tx_98765",
  "status": "categorized",
  "amount": "-42.50",
  "currency": "USD",
  "description": "STARBUCKS #1234 SEATTLE WA",
  "merchant": "Starbucks",
  "category": "Dining & Coffee",
  "category_source": "llm",
  "anomaly_score": 0.02,
  "occurred_at": "2026-09-05T14:30:00Z",
  "categorized_at": "2026-09-05T14:30:04Z"
}
```

- `status`: `pending_categorization` → `categorized` (`categorization_failed` exists in the enum
  but the service always falls back to the rules engine rather than leaving a row failed).
- `category`: display name from the taxonomy below, or `null` while pending.
- `category_source`: `rules`, `llm` or `user`.
- `anomaly_score`: 0–0.99, computed deterministically against the account's own history.
  Inflows always score 0.

### `GET /transactions` → `200 OK`

Newest first, cursor-paginated, across all of the caller's accounts unless filtered.

| Query | Notes |
|---|---|
| `account_id` | Restrict to one owned account (`404` if not owned). |
| `category` | Category **slug**, e.g. `dining_coffee`. |
| `status` | `pending_categorization`, `categorized`, `categorization_failed`. |
| `from`, `to` | `occurred_at` bounds (ISO 8601). |
| `cursor` | Opaque; pass back `next_cursor` from the previous page. |
| `limit` | 1–100, default 25. |

```json
{ "items": [ { "...": "TransactionRead as above" } ], "next_cursor": "2026-09-05T14:30:00+00:00|tx_m1a2...", "limit": 25 }
```

`next_cursor` is `null` on the last page.

### `PATCH /transactions/{id}` → `200 OK`

User correction of the category. Accepts a slug or a display name (`"groceries"` or
`"Groceries"`); unknown categories return `422`.

```json
{ "category": "Groceries" }
```

Sets `category_source` to `user`, writes an audit entry, and invalidates the cached LLM
classification for that merchant so the rejected answer is not reused. It does not publish a new
event.

### Category taxonomy

| Slug | Name | Slug | Name |
|---|---|---|---|
| `dining_coffee` | Dining & Coffee | `entertainment` | Entertainment |
| `groceries` | Groceries | `travel` | Travel |
| `transport` | Transport | `health` | Health |
| `fuel` | Fuel | `education` | Education |
| `housing` | Housing | `subscriptions` | Subscriptions |
| `utilities` | Utilities | `income` | Income |
| `shopping` | Shopping | `transfers` | Transfers |
| `electronics` | Electronics | `fees` | Fees |
| | | `other` | Other |

Source of truth: `libs/db/categories.py`.

---

## Assistant

### `GET /assistant/status` → `200 OK`

Authenticated configuration status: `mode` is `llm`, `disabled`, or `unconfigured`, with a
user-facing `message` and nullable `model`. A configured mode does not guarantee a successful
provider call; the chat response separately identifies whether its wording came from the model.
The response never includes credentials.

### `POST /assistant/chat` → `200 OK`

Read-only Q&A over the caller's own transactions.

```json
{ "message": "How much did I spend on dining last month?", "conversation_id": "conv_555" }
```

`message` is 1–1000 chars. Omit `conversation_id` to start a new conversation; the response
returns the id to use for follow-ups.
Optional `currency` is a three-letter code (case-insensitive); it defaults to the primary
account currency. Answers disclose spending excluded in other currencies, including empty
results, and retain that disclosure after LLM rewording. No FX conversion is performed.

```json
{
  "conversation_id": "conv_555",
  "reply": "You spent $141.12 on Dining & Coffee in August 2026, across 9 transactions. That's 124% more than the previous period.",
  "sources": [ { "type": "aggregate", "category": "Dining & Coffee", "period": "August 2026" } ],
  "reply_source": "llm"
}
```

- `reply_source` is `llm` when the model reworded the answer and `draft` when the deterministic
  answer was returned (LLM disabled, rate limited, circuit open, or invalid output). The figures
  are identical either way; every number is computed in SQL.
- `sources[].type` is `aggregate` or `transactions`.
- Nullable `fallback_reason` explains a draft reply: `disabled`, `unconfigured`, `rate_limited`,
  `provider_unavailable`, `provider_timeout`, or `invalid_response`. The entire model attempt,
  including retries, is bounded to 12 s (or a smaller configured timeout).
- Greetings and capability questions explain supported questions instead of returning an
  unrelated total. Follow-ups such as “And on groceries?” retain the previous explicit period
  in the same user-owned conversation unless the new question names another period.
- Understood periods: "last month", "last week", "this week", "this year", "last N
  days/weeks/months", a month name ("in August"), defaulting to the current month. Categories are
  matched from everyday words ("dining", "gas", "rent", "streaming", …). Words like "largest" or
  "biggest" add the top individual transactions.
- Largest outflows are selected from the full owned ledger in the requested currency and
  period, with category filtering applied before ordering and limiting.

**Guardrail.** Messages that ask the assistant to move money, pay, delete, edit, create or mark
anything are refused with `400 READ_ONLY_ASSISTANT` before any retrieval or model call. The
message points to the endpoint that can do it. Questions about history are allowed even when they
contain those verbs: "Move $50 to savings" is refused; "How much did I transfer to savings last
month?" is answered.

### `GET /assistant/conversations/{id}` → `200 OK`

```json
{
  "conversation_id": "conv_555",
  "turns": [
    { "role": "user", "content": "How much did I spend on dining last month?", "at": "2026-09-05T14:31:00Z" },
    { "role": "assistant", "content": "You spent $141.12 ...", "at": "2026-09-05T14:31:00Z" }
  ]
}
```

History is kept in Redis for 24 hours and capped at the last 20 turns. It is keyed by user, so
another user's conversation id returns an empty `turns` list rather than an error.

---

## Insights

### `GET /insights/summary` → `200 OK`

Spending for the **current period to date**, in **one currency**, with a natural-language
summary generated from the aggregates only — the model never sees individual transactions.

| Query | Notes |
|---|---|
| `account_id` | Optional; defaults to all of the caller's accounts. |
| `period` | `monthly` (default) or `weekly`. |
| `currency` | Optional ISO 4217 code (case-insensitive). Defaults to the currency most of the selected accounts are held in (oldest account breaks ties). |
| `as_of` | Optional reporting end time with timezone; defaults to server time. Naive or future timestamps return `422`. Accepted offsets are converted to UTC. |

```json
{
  "period": "2026-09",
  "date_from": "2026-09-01T00:00:00Z",
  "date_to": "2026-09-15T12:00:00Z",
  "currency": "USD",
  "total_spend": "2140.55",
  "by_category": [
    { "category": "Groceries", "amount": "480.10", "transaction_count": 12 },
    { "category": "Dining & Coffee", "amount": "312.40", "transaction_count": 18 }
  ],
  "summary": "September spending is up 8% on August, driven mainly by groceries and dining.",
  "summary_source": "llm",
  "change_vs_previous_pct": 8.2,
  "other_currencies": ["EUR"]
}
```

- Every amount is in `currency`. Transactions in other currencies are **left out, never
  added in**; `other_currencies` lists the ones with spending in the period so a client can
  say so or request them with `?currency=`.
- Only outflows count toward `total_spend`.
- `date_from` and `date_to` give the actual inclusive UTC window. The dashboard uses these
  server-provided bounds when paginating transactions so Money in and Money out share a window.
- `period` is `YYYY-MM` for monthly and `YYYY-Www` (ISO week) for weekly.
- `summary_source` is `llm`, `cache` (identical aggregates seen before) or `fallback`
  (templated sentence; LLM disabled or failed).
- `change_vs_previous_pct` compares with the whole previous period, in the same currency, and
  is `null` when there is nothing to compare with.

### `GET /insights/trends` → `200 OK`

Pure SQL, no LLM. Category spend over the last N periods, oldest first.

| Query | Notes |
|---|---|
| `account_id` | Optional. |
| `period` | `monthly` (default) or `weekly`. |
| `periods` | 1–24, default 6. |
| `currency` | Optional, as for `/summary`. |

```json
{
  "currency": "USD",
  "periods": [
    { "period": "2026-08", "total": "1980.00", "by_category": [ { "category": "Groceries", "amount": "455.20", "transaction_count": 11 } ] },
    { "period": "2026-09", "total": "2140.55", "by_category": [ "..." ] }
  ]
}
```

---

## Notifications

Alerts are created by the Notifications service's Kafka consumer, never by clients.

### `GET /notifications` → `200 OK`

| Query | Notes |
|---|---|
| `limit` | 1–100, default 50. Newest first. |

```json
{
  "notifications": [
    {
      "id": "notif_m1a2b3c4d5e6f7",
      "type": "anomaly_flagged",
      "transaction_id": "tx_m1a2b3c4d5e6f7",
      "message": "Unusual transaction: $842.00 at Best Buy.",
      "created_at": "2026-09-05T14:30:10Z",
      "read": false
    }
  ],
  "unread_count": 1
}
```

`unread_count` counts unread items within the returned page.

| `type` | Raised when | Dedupe |
|---|---|---|
| `anomaly_flagged` | `transaction.anomaly_flagged` received (score ≥ `ANOMALY_ALERT_THRESHOLD`, default 0.8). | one per transaction |
| `large_transaction` | An outflow of at least `LARGE_TRANSACTION_THRESHOLD` (default 500). Deposits never alert. | one per transaction |
| `budget_threshold` | Month-to-date spend in the transaction's category **and currency** reaches `BUDGET_MONTHLY_LIMIT` (default 1500). | one per user, currency, category and month |

Channel delivery is tracked separately from the public `read` flag. A stored alert with pending
delivery is sent again when its event is replayed; successfully delivered alerts are skipped.
Delivery is at least once: a crash after sending but before recording success can send again.

### `PATCH /notifications/{id}` → `200 OK`

```json
{ "read": true }
```

Returns the updated notification.

---

## Internal event contract (Kafka)

Not callable by clients, but it is the contract between Transactions and Notifications and is
versioned like a public API. Source: `libs/events/schemas.py`.

- **Topic:** `transactions.events`. **Key:** `account_id`, so one account's events stay ordered.
- **Envelope:** every event has `event_type`, `schema_version` (currently `1`) and
  `published_at`.
- **Compatibility:** add fields, never repurpose or remove them. Consumers ignore unknown fields
  and skip unknown `event_type`s, so a producer can deploy ahead of its consumers.
- **Delivery:** published after the database commit. Successfully published records are consumed
  at least once; consumers are idempotent. Handler and offset-commit failures retry the same
  record before advancing, committing only its partition's next offset on success. Invalid JSON,
  non-object payloads and events that fail schema validation are logged and skipped.
  Rebalance callbacks invalidate the held record, resume polling to rejoin, and prevent stale
  work from committing after ownership changes.

### `transaction.categorized`

Published for every categorized transaction.

```json
{
  "event_type": "transaction.categorized",
  "schema_version": 1,
  "published_at": "2026-09-05T14:30:04Z",
  "transaction_id": "tx_m1a2b3c4d5e6f7",
  "account_id": "acct_123",
  "user_id": "usr_m1a2b3c4d5e6f7",
  "category": "dining_coffee",
  "category_source": "llm",
  "amount": "-42.50",
  "currency": "USD",
  "merchant": "Starbucks",
  "anomaly_score": 0.02,
  "occurred_at": "2026-09-05T14:30:00Z"
}
```

Note that `category` here is the slug, not the display name.

### `transaction.anomaly_flagged`

Published in addition to the above when `anomaly_score ≥ ANOMALY_ALERT_THRESHOLD`.

```json
{
  "event_type": "transaction.anomaly_flagged",
  "schema_version": 1,
  "published_at": "2026-09-05T14:30:04Z",
  "transaction_id": "tx_m1a2b3c4d5e6f7",
  "account_id": "acct_123",
  "user_id": "usr_m1a2b3c4d5e6f7",
  "amount": "-842.00",
  "currency": "USD",
  "merchant": "Best Buy",
  "anomaly_score": 0.91,
  "reason": "amount_outlier"
}
```

---

## Service-to-service authentication

Downstream services accept either:

1. `X-Internal-Token: <INTERNAL_SERVICE_TOKEN>` plus `X-User-Id: <user id>` — what the Gateway
   sends; or
2. a user `Authorization: Bearer` access token — convenient for calling a service directly in
   local development and tests.

The Gateway strips any client-supplied `X-Internal-Token`, `X-User-Id` and `Authorization`
headers before forwarding and sets its own. The Gateway itself accepts only a user JWT, so
presenting the internal token at the edge returns `401`.

---

## Ops endpoints (every service, not under `/api/v1`)

| Endpoint | Purpose |
|---|---|
| `GET /health` | Liveness. `{"status": "ok", "service": "<name>"}` whenever the process is up. |
| `GET /ready` | Readiness. `200 {"status": "ready", "checks": {...}}` or `503 "not_ready"` with the failing dependency. |
| `GET /metrics` | Prometheus text format. |
| `GET /docs`, `GET /openapi.json` | Swagger UI and OpenAPI schema. |

Readiness checks per service: all check the database; Transactions and Assistant also check
Redis when `LLM_ENABLED=true`. See [`docs/OPERATIONS.md`](docs/OPERATIONS.md) for metrics and
degraded-mode behavior.
