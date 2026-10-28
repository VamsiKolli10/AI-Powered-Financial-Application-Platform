# Contributing

Use Python 3.12 and run commands from the repository root.

```bash
uv venv --python 3.12
source .venv/bin/activate
uv pip install -e ".[dev]"
pre-commit install
cp .env.example .env
```

The example credentials are for local development. Keep real credentials and
financial data out of commits, fixtures, screenshots, logs, and issue reports.
Use synthetic data for tests and demos.

Before opening a pull request:

```bash
make lint
make test
python -m scripts.eval_categorization --mode rules --min-accuracy 0.90
pre-commit run --all-files
git diff --check
```

The default test fixtures use SQLite and mocked services. Passing these tests
does not establish PostgreSQL concurrency behavior or real Kafka delivery.
Describe any live-service validation separately in your pull request.

Report fixtures that must fall in "this month" should use
`tests.conftest.within_current_month()` rather than "N days ago" or a fixed date,
which break on the first days of a month.

For dashboard changes, run the frontend checks too:

```bash
cd web
npm ci
npm test        # Vitest unit and component tests
npm run build   # type-check and production build
```

Keep changes focused, add meaningful regression coverage for behavior changes,
and include an Alembic migration when changing the database schema. Explain the
problem, resulting behavior, and validation in the pull request.

Generate schema migrations with `alembic revision --autogenerate -m "Describe the change"`.
Files use `YYYYMMDDHHMMSS_description.py`, with timestamps in UTC. Alembic generates a unique
internal revision ID and records the parent in `down_revision`; these IDs determine migration
order. Keep existing IDs unchanged even when renaming files, so databases already migrated can
continue upgrading. The two original files use their first Git commit timestamp; the delivery
migration uses its creation timestamp.

Keep the docs in step with the code:

| When you change… | Update |
|---|---|
| An endpoint, schema, error code, or event | [API_DESIGN.md](API_DESIGN.md) |
| A setting in `libs/common/config.py` | [docs/CONFIGURATION.md](docs/CONFIGURATION.md) and `.env.example` |
| A dependency, failure mode, metric, or probe | [docs/OPERATIONS.md](docs/OPERATIONS.md) |
| Service boundaries, data model, or a design trade-off | [ARCHITECTURE.md](ARCHITECTURE.md) |
| Setup steps or headline capabilities | [README.md](README.md) |

See [SECURITY.md](SECURITY.md) for vulnerability reporting and
[BUILD_PLAN.md](BUILD_PLAN.md) for implementation status.
