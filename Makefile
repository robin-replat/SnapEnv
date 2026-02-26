.PHONY: help dev up down migrate test lint security fmt check clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ── Setup ─────────────────────────────────────

dev: ## Install dev dependencies and setup pre-commit hooks
	uv sync --dev
	pre-commit install

# ── Docker ────────────────────────────────────

up: ## Start all services (API + PostgreSQL)
	docker compose up -d

up-db: ## Start only PostgreSQL (for local dev without Docker API)
	docker compose up -d postgres

down: ## Stop all services
	docker compose down

logs: ## Tail logs from all services
	docker compose logs -f

logs-api: ## Tail logs from the API only
	docker compose logs -f api

rebuild: ## Rebuild images and restart services
	docker compose up -d --build

# ── Database ──────────────────────────────────

migrate: ## Run database migrations
	uv run alembic upgrade head

migration: ## Create a new migration (usage: make migration msg="add users table")
	uv run alembic revision --autogenerate -m "$(msg)"

# ── Quality ───────────────────────────────────

test: ## Run tests with coverage
	uv run pytest

lint: ## Run linters (ruff + mypy + bandit)
	uv run ruff check src/ tests/
	uv run mypy src/ --ignore-missing-imports

security: ## Run secret scanning (gitleaks via pre-commit)
	uv run bandit -c pyproject.toml -r src/ tests/
	pre-commit run gitleaks --all-files

fmt: ## Format code (ruff fix + ruff format)
	uv run ruff check --fix src/ tests/
	uv run ruff format src/ tests/

check: fmt lint security test ## Run all checks: format, lint, security, then test

# ── Run ───────────────────────────────────────

run: ## Start the API with hot reload
	uv run uvicorn src.api.main:app --reload

# ── Cleanup ───────────────────────────────────

clean: ## Remove build artifacts and caches
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +
	find . -type d -name .mypy_cache -exec rm -rf {} +
	rm -rf dist/ build/ *.egg-info/ htmlcov/ .coverage coverage.xml
