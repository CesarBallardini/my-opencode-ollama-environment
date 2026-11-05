.DEFAULT_GOAL := help

.PHONY: help install qa lint format types test integration e2e-ci coverage security secrets precommit clean

help: ## Show this list of available targets
	@grep -E '^[a-zA-Z0-9_-]+:.*## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*## "}; {printf "\033[36m%-16s\033[0m %s\n", $$1, $$2}'

install: ## Sync the tool environment (from the committed lockfile) and install the git hooks
	uv sync --all-groups --frozen
	uv run --frozen pre-commit install

qa: lint types coverage security ## Run the quality gates: lint, types, tests with coverage, security

lint: ## Check formatting and lint rules without modifying files
	uv run --frozen ruff check .
	uv run --frozen ruff format --check .

format: ## Auto-fix formatting and lint issues
	uv run --frozen ruff format .
	uv run --frozen ruff check --fix .

types: ## Run both type checkers (pyright + pyrefly)
	uv run --frozen pyright
	uv run --frozen pyrefly check

test: ## Run the test suite
	uv run --frozen pytest

integration: ## Run the tests against the real OS; the firewall ones need an elevated console and skip otherwise
	uv run --frozen pytest -m integration

e2e-ci: ## Install, verify and uninstall for real, on a disposable elevated machine only, never a laptop in use
	powershell -NoProfile -ExecutionPolicy Bypass -File tests/e2e/ci-e2e.ps1

coverage: ## Run the test suite with coverage and enforce the floor from .coveragerc
	uv run --frozen pytest --cov=. --cov-config=.coveragerc --cov-report=term-missing --cov-report=html

# No runtime dependencies ship from here, so there is no license gate: only the
# tool environment is audited.
security: ## Run every security scan (SAST + CVEs + secrets)
	uv run --frozen bandit -c bandit.yaml -r .
	uv run --frozen pip-audit --skip-editable
	osv-scanner --lockfile=./uv.lock
	$(MAKE) secrets

secrets: ## Scan the working tree for committed secrets (needs gitleaks on PATH)
	gitleaks dir . --redact --verbose -c .gitleaks.toml

precommit: ## Run all pre-commit hooks against every file
	uv run --frozen pre-commit run --all-files

clean: ## Remove cache and coverage artifacts
	rm -rf htmlcov/ .coverage coverage.xml .pytest_cache/ .ruff_cache/ .pyrefly_cache/ .hypothesis/
