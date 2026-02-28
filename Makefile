.PHONY: help dev up down migrate test lint security fmt check clean helm-secrets

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ── Setup ─────────────────────────────────────

dev: ## Install dev dependencies and setup pre-commit hooks
	uv sync --dev
	pre-commit install

helm-secrets: ## Generate values-local.yaml from .env for local Helm deployments
	@echo "Generating infra/helm/snapenv/values-local.yaml from .env..."
	@python3 -c "\
	import os; \
	lines = open('.env').readlines(); \
	env = {l.split('=',1)[0].strip(): l.split('=',1)[1].strip() for l in lines if '=' in l and not l.startswith('#')}; \
	f = open('infra/helm/snapenv/values-local.yaml', 'w'); \
	f.write('postgresql:\n'); \
	f.write('  auth:\n'); \
	f.write(f'    username: \"{env.get(\"POSTGRES_USER\", \"snapenv\")}\"\n'); \
	f.write(f'    password: \"{env.get(\"POSTGRES_PASSWORD\", \"snapenv\")}\"\n'); \
	f.write(f'    database: \"{env.get(\"POSTGRES_DB\", \"snapenv\")}\"\n'); \
	f.close()"
	@echo "Done! values-local.yaml created."

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


# ── Run API ───────────────────────────────────────

run: ## Start the API with hot reload
	uv run uvicorn src.api.main:app --reload

# ── Kubernetes ────────────────────────────────

cluster-create: ## Create the k3d cluster with nginx ingress and ArgoCD
	chmod +x scripts/setup-cluster.sh
	./scripts/setup-cluster.sh

cluster-delete: ## Delete the k3d cluster
	k3d cluster delete snapenv

k8s-build: ## Build Docker image and import into k3d
	docker build -t snapenv:local -f .docker/Dockerfile.api .
	k3d image import snapenv:local -c snapenv

k8s-deploy: k8s-build ## Build, import to k3d, and deploy with Helm
	@helm upgrade --install snapenv ./infra/helm/snapenv \
		-f ./infra/helm/snapenv/values-local.yaml \
		--set image.repository=snapenv \
		--set image.tag=local \
		--set image.pullPolicy=Never \
		--wait --timeout 120s
	@echo ""
	@echo "✓ Deployed! Access at: http://snapenv.localhost"

k8s-status: ## Show status of all K8s resources
	@echo "=== Pods ==="
	@kubectl get pods
	@echo "\n=== Services ==="
	@kubectl get svc
	@echo "\n=== Ingress ==="
	@kubectl get ingress

k8s-logs: ## Tail logs from the API pod
	kubectl logs -f -l app=snapenv,component=api

argocd-ui: ## Open ArgoCD UI (port-forward)
	@echo "ArgoCD UI: https://localhost:8080"
	@echo "User: admin"
	@echo "Password: $$(kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d)"
	@echo ""
	kubectl port-forward svc/argocd-server -n argocd 8080:443

# ── Cleanup ───────────────────────────────────

clean: ## Remove build artifacts and caches
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +
	find . -type d -name .mypy_cache -exec rm -rf {} +
	rm -rf dist/ build/ *.egg-info/ htmlcov/ .coverage coverage.xml
