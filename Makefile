.PHONY: help install test test-cov lint format migrate seed up down logs clean

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-12s\033[0m %s\n", $$1, $$2}'

install:  ## Create a virtualenv and install the project with dev extras
	uv venv --python 3.12
	uv pip install -e ".[dev]"
	pre-commit install

test:  ## Run the test suite (SQLite-backed, no Docker required)
	pytest -q

test-cov:  ## Run tests with a coverage report
	pytest --cov=libs --cov=services --cov-report=term-missing

lint:  ## ruff + black --check + mypy
	ruff check .
	black --check .
	mypy libs services

format:  ## Auto-fix lint and formatting
	ruff check . --fix
	black .

migrate:  ## Apply database migrations
	alembic upgrade head

seed:  ## Load demo data
	python -m scripts.seed

eval:  ## Categorization accuracy against the labeled sample (rules engine)
	python -m scripts.eval_categorization --mode both --show-misses

smoke:  ## Smoke load test against a running gateway
	python -m scripts.smoke_load --url http://localhost:8000 --requests 200 --concurrency 20

up:  ## Start the whole platform locally
	docker compose up --build

down:  ## Stop the platform
	docker compose down

logs:  ## Tail service logs
	docker compose logs -f gateway transactions assistant insights notifications

clean:  ## Remove caches and build artifacts
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage dist build
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
