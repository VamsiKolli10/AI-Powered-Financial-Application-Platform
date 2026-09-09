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

Keep changes focused, add meaningful regression coverage for behavior changes,
and include an Alembic migration when changing the database schema. Explain the
problem, resulting behavior, and validation in the pull request. Update the
README and API documentation when public behavior changes.

See [SECURITY.md](SECURITY.md) for vulnerability reporting and
[BUILD_PLAN.md](BUILD_PLAN.md) for implementation status.
