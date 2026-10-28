# Configuration

Every backend service reads the same settings class, `libs/common/config.py`, from environment
variables (case-insensitive) and then from `.env` and `.env.local` in the working directory.
Real environment variables win over the files. Start from the template:

```bash
cp .env.example .env
```

`.env.example` uses Docker Compose hostnames (`postgres`, `redis`, `kafka`, `transactions`, …).
When running services directly on your machine, change those to `localhost` and the published
ports listed in [Service URLs](#downstream-service-urls-gateway-only).

## Choosing an LLM mode

| Goal | Set |
|---|---|
| Run with no provider calls (deterministic rules, templated summaries, draft assistant replies) | `LLM_ENABLED=false` |
| Use OpenAI | `LLM_ENABLED=true` and a real `OPENAI_API_KEY` |
| Use OpenRouter's free tier | an OpenRouter key in `OPENAI_API_KEY`, plus the endpoint/model below |
| Use Groq's free tier | a Groq key in `OPENAI_API_KEY`, plus the endpoint/model below |
| Use an OpenAI-compatible provider, LLM gateway, or local server | additionally `LLM_BASE_URL` and `LLM_MODEL` |

The template starts with `LLM_ENABLED=false` and an empty key. For live AI, configure a valid
`OPENAI_API_KEY`, set `LLM_ENABLED=true`, and recreate the affected containers with
`docker compose up -d --build`. Empty keys and template placeholders such as `sk-replace-me`
are treated as unconfigured locally and rejected in staging/production when AI is enabled.
The assistant reports its mode through authenticated `GET /api/v1/assistant/status`;
the dashboard shows ledger mode separately from a model-generated reply.

### OpenRouter free-tier setup

Create a key in [OpenRouter's key settings](https://openrouter.ai/settings/keys), then set these
values in your local `.env`. Keep the real key out of Git and chat:

```dotenv
OPENAI_API_KEY=your-openrouter-api-key
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_MODEL=openrouter/free
LLM_RATE_LIMIT_PER_MINUTE=10
LLM_ENABLED=true
```

`OPENAI_API_KEY` is the app's existing setting name for the selected provider's key.
With this endpoint, the existing OpenAI SDK sends requests to OpenRouter using your OpenRouter
key. You do not need an OpenAI key or a separate provider SDK. See OpenRouter's
[quickstart](https://openrouter.ai/docs/quickstart).

The [Free Models Router](https://openrouter.ai/docs/guides/routing/routers/free-router) selects
from currently available free models, filtering for required capabilities. The adapter also
requires providers to honor JSON mode and requests thinking to be disabled for this router,
so the app's short output budgets remain available for the JSON reply. Individual free models
can be rate-limited upstream even when your account has quota remaining.

You can select a particular free model by using a `:free` model ID instead. For example,
[Gemma 4 26B A4B (free)](https://openrouter.ai/google/gemma-4-26b-a4b-it:free) supports JSON output.
Free endpoints can change or become unavailable; consult the
[model catalog](https://openrouter.ai/models) when changing `LLM_MODEL` and verify support for
`response_format`. Model choice and wording may vary between free-router requests.

OpenRouter's [Free plan](https://openrouter.ai/pricing) currently allows 50 requests per day.
Chat, categorization, summaries, and retries share your provider allowance. The app's rate
setting above limits requests per workflow per minute; it does not increase the daily provider
quota. Check [OpenRouter limits](https://openrouter.ai/docs/api/reference/limits) for current
details. A paid model or credit purchase is not required for this free configuration.

Recreate the backend services to load the environment:

```bash
docker compose up -d --build --no-deps --force-recreate transactions assistant insights
```

A successful model reply has `reply_source=llm` in the assistant API response. The dashboard's
configured-mode indicator alone does not verify a live provider reply. Provider errors or
timeouts return a ledger answer with an explanation.

### Groq free-tier setup

Create a key in the [Groq Console](https://console.groq.com/keys) and stay on the Free plan.
Set these values in your local `.env` file; keep the real key out of Git and chat:

```dotenv
OPENAI_API_KEY=your-groq-api-key
LLM_BASE_URL=https://api.groq.com/openai/v1
LLM_MODEL=qwen/qwen3.8-27b
LLM_RATE_LIMIT_PER_MINUTE=20
LLM_ENABLED=true
```

`OPENAI_API_KEY` is the app's existing setting name for the selected provider's key.
With the Groq endpoint, requests go to Groq using your Groq key. An OpenAI key or subscription
is not required. The existing adapter uses Groq's documented
[OpenAI-compatible API](https://console.groq.com/docs/openai).

[Qwen 3.8 27B](https://console.groq.com/docs/model/qwen/qwen3.8-27b) supports JSON Object Mode,
which all three AI features require. Groq's API defaults this model to non-thinking mode, fitting
the app's short output budgets. It is a preview model, suitable for local evaluation; check
[supported models](https://console.groq.com/docs/models) before deploying it to production.

The free tier has request and token quotas. Check your account's
[limits](https://console.groq.com/settings/limits) for the exact allowance and the
[rate-limit documentation](https://console.groq.com/docs/rate-limits) for general limits.
The app setting above limits each AI workflow's requests; provider quotas still apply to the
combined traffic and token usage. Paid Developer-tier upgrades are optional; see
[billing details](https://console.groq.com/docs/billing-faqs).

Recreate the backend services so Compose loads the updated environment:

```bash
docker compose up -d --no-deps --force-recreate transactions assistant insights
```

After signing in, ask the assistant a question. A successful provider reply has
`reply_source=llm` in the API response; provider failures still return a ledger answer with an
explanation. The configured mode alone does not prove the provider call succeeded.

## Reference

### Core

| Variable | Default | Used by | Notes |
|---|---|---|---|
| `ENVIRONMENT` | `local` | all | `local`, `test`, `staging`, `production`. In `staging`/`production` the service refuses to start with development secrets (see [Startup validation](#startup-validation)). |
| `LOG_LEVEL` | `INFO` | all | Structured JSON logs via structlog. |
| `SERVICE_NAME` | `service` | all | Label used in logs and metrics. |
| `SERVICE` | `gateway` | container | Not a setting: the Docker image runs `services.${SERVICE}.main:app`. |

### PostgreSQL

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://finai:finai@localhost:5432/finai` | Must use an async driver. Tests use `sqlite+aiosqlite`. Also read by Alembic. |
| `DB_ECHO` | `false` | Log every SQL statement. |
| `DB_POOL_SIZE` | `10` | Per process. |
| `DB_MAX_OVERFLOW` | `5` | Per process. |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | `finai` | Only used by the Compose `postgres` container. Keep in sync with `DATABASE_URL`. |

### Redis

| Variable | Default | Notes |
|---|---|---|
| `REDIS_URL` | `redis://localhost:6379/0` | Use `rediss://` for TLS (ElastiCache). |

The Gateway always uses Redis for rate limiting. Transactions, Assistant and Insights connect to
Redis only when `LLM_ENABLED=true` (LLM cache, LLM rate limit, chat history); with the LLM
disabled they keep chat history in process memory.

### Kafka

| Variable | Default | Notes |
|---|---|---|
| `KAFKA_ENABLED` | `true` | `false` swaps the producer for a logging no-op and does not start the Notifications consumer. No new alerts are created. |
| `KAFKA_BOOTSTRAP_SERVERS` | `localhost:9092` | Comma-separated. |
| `KAFKA_CONSUMER_GROUP` | `finai` | Notifications joins `<group>.notifications`. |
| `KAFKA_SECURITY_PROTOCOL` | `PLAINTEXT` | `PLAINTEXT`, `SSL`, `SASL_PLAINTEXT`, `SASL_SSL`. MSK uses `SASL_SSL`. |
| `KAFKA_SASL_MECHANISM` | `SCRAM-SHA-512` | `PLAIN`, `SCRAM-SHA-256`, `SCRAM-SHA-512`. Used only with a `SASL_*` protocol. |
| `KAFKA_SASL_USERNAME` / `KAFKA_SASL_PASSWORD` | empty | Required with a `SASL_*` protocol outside local. |

With `KAFKA_ENABLED=true`, Transactions and Notifications connect to the broker at startup and
fail to start if it is unreachable.

### LLM

| Variable | Default | Notes |
|---|---|---|
| `LLM_ENABLED` | `true` | Master switch for every AI path. |
| `OPENAI_API_KEY` | empty | Key for the selected OpenAI-compatible provider, including OpenRouter or Groq. Empty or a template placeholder leaves the provider unconfigured locally. |
| `LLM_BASE_URL` | unset | OpenAI-compatible endpoint override. |
| `LLM_MODEL` | `gpt-4o-mini` | |
| `LLM_TIMEOUT_SECONDS` | `20` | Per provider request. Chat additionally bounds the whole model attempt to at most 12 s so it can return a ledger answer before the Gateway's 15 s timeout. |
| `LLM_CACHE_TTL_SECONDS` | `604800` (7 days) | Categorization and summary cache TTL. |
| `LLM_RATE_LIMIT_PER_MINUTE` | `120` | Provider calls per minute, shared across replicas via Redis. |

### Auth

| Variable | Default | Notes |
|---|---|---|
| `JWT_SECRET` | `dev-only-change-me` | ≥ 32 characters outside local. Changing it invalidates all tokens. |
| `JWT_ALGORITHM` | `HS256` | |
| `JWT_EXPIRE_MINUTES` | `60` | Access token lifetime. Refresh tokens last 30 days (fixed). |
| `INTERNAL_SERVICE_TOKEN` | `dev-internal-token` | Shared secret between the Gateway and downstream services. Must match across all services. |

### Alerting thresholds (Notifications; anomaly threshold also used by Transactions)

| Variable | Default | Notes |
|---|---|---|
| `ANOMALY_ALERT_THRESHOLD` | `0.8` | Transactions publishes `transaction.anomaly_flagged` at or above this score. |
| `LARGE_TRANSACTION_THRESHOLD` | `500` | Outflow amount that raises a `large_transaction` alert. |
| `BUDGET_MONTHLY_LIMIT` | `1500` | Month-to-date spend per category that raises a `budget_threshold` alert. |

### Rate limits (Gateway, per user per minute)

| Variable | Default | Applies to |
|---|---|---|
| `RATE_LIMIT_STANDARD` | `100` | All proxied routes except the LLM tier |
| `RATE_LIMIT_LLM` | `20` | `/assistant/*`, `/insights/summary` |

### Downstream service URLs (Gateway only)

| Variable | Default (bare metal) | Compose / Kubernetes value |
|---|---|---|
| `TRANSACTIONS_URL` | `http://localhost:8001` | `http://transactions:8000` |
| `ASSISTANT_URL` | `http://localhost:8002` | `http://assistant:8000` |
| `INSIGHTS_URL` | `http://localhost:8003` | `http://insights:8000` |
| `NOTIFICATIONS_URL` | `http://localhost:8004` | `http://notifications:8000` |

## Startup validation

When `ENVIRONMENT` is `staging` or `production`, settings validation fails fast if:

- `JWT_SECRET` is the default or shorter than 32 characters;
- `INTERNAL_SERVICE_TOKEN` is the default or empty;
- `LLM_ENABLED=true` and `OPENAI_API_KEY` is empty;
- `KAFKA_ENABLED=true`, a `SASL_*` protocol is set, and the SASL username or password is empty.

## Where configuration comes from in each environment

| Environment | Non-secret values | Secrets |
|---|---|---|
| Local, no Docker | `.env` | `.env` |
| Docker Compose | `.env` (via `env_file`) plus `SERVICE`/`SERVICE_NAME` per service | `.env` |
| Kubernetes (`infra/k8s`) | `platform-config` ConfigMap from `kustomization.yaml` (`LLM_ENABLED=false`) | `platform-secrets` generated Secret — **local development values only** |
| AWS (`infra/k8s-aws`) | Base ConfigMap merged with `ENVIRONMENT=staging`, `SASL_SSL`; `platform-aws-config` adds the MSK brokers | `platform-secrets` created from AWS Secrets Manager by `scripts/deploy-aws.sh` |

The web dashboard has no runtime configuration: it calls same-origin `/api/v1`, and nginx
proxies `/api/` to `http://gateway:8000` (`web/nginx.conf`). For `npm run dev`, the Vite dev
server's proxy settings live in `web/vite.config.ts`.
