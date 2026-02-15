.PHONY: help dev up down migrate test lint fmt check clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ── Setup ─────────────────────────────────────

dev: ## Install dev dependencies and setup pre-commit hooks
	uv sync --dev
	pre-commit install

# ── Docker ────────────────────────────────────

up: ## Start PostgreSQL
	docker compose up -d

down: ## Stop PostgreSQL
	docker compose down

# ── Database ──────────────────────────────────

migrate: ## Run database migrations
	uv run alembic upgrade head

migration: ## Create a new migration (usage: make migration msg="add users table")
	uv run alembic revision --autogenerate -m "$(msg)"

# ── Quality ───────────────────────────────────

test: ## Run tests with coverage
	uv run pytest

lint: ## Run linters (ruff + mypy)
	uv run ruff check src/ tests/
	uv run mypy src/ --ignore-missing-imports

fmt: ## Format code (ruff fix + black)
	uv run ruff check --fix src/ tests/
	uv run black src/ tests/

check: fmt lint test ## Run all checks: format, lint, then test

# ── Run ───────────────────────────────────────

run: ## Start the API with hot reload
	uv run uvicorn src.api.main:app --reload

# ── Cleanup ───────────────────────────────────

clean: ## Remove build artifacts and caches
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +
	find . -type d -name .mypy_cache -exec rm -rf {} +
	rm -rf dist/ build/ *.egg-info/ htmlcov/ .coverage coverage.xml
