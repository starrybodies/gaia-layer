.DEFAULT_GOAL := help
SHELL := /bin/bash

PIPELINE := cd pipeline && uv run
API_PORT ?= 8811
CONSOLE_PORT ?= 3311

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------- setup

.PHONY: setup
setup: setup-py setup-ts ## Install every dependency (Python + Node)

.PHONY: setup-py
setup-py: ## Provision the pinned interpreter and sync Python deps
	cd pipeline && uv sync --all-extras

.PHONY: setup-ts
setup-ts: ## Install Node workspace deps
	pnpm install

# ---------------------------------------------------------------- schema

.PHONY: schema
schema: ## Regenerate JSON Schema from Pydantic, then Zod from JSON Schema
	$(PIPELINE) python -m gaia_pipeline.schemas.export --out ../docs/schema
	pnpm --filter @gaia/core generate

.PHONY: schema-check
schema-check: schema ## Fail if generated schema/Zod artifacts are stale
	@git diff --exit-code -- docs/schema core/src/generated \
		|| (echo ""; echo "Generated schema artifacts are stale. Run 'make schema' and commit."; exit 1)

# ---------------------------------------------------------------- run

.PHONY: dev
dev: ## Boot API + console (MCP server speaks stdio; see RUNBOOK)
	API_PORT=$(API_PORT) CONSOLE_PORT=$(CONSOLE_PORT) pnpm dev

.PHONY: mcp
mcp: ## Run the MCP server on stdio
	pnpm --filter @gaia/mcp-server start

# ---------------------------------------------------------------- pipeline

.PHONY: ingest
ingest: ## Run the full ingestion pipeline for the configured AOI
	$(PIPELINE) gaia ingest all

.PHONY: coverage
coverage: ## Print what is in the data lake
	$(PIPELINE) gaia coverage

.PHONY: seed
seed: ## Cold-start: ingest 12 months for the pilot AOI end to end
	$(PIPELINE) gaia seed

# ---------------------------------------------------------------- quality

.PHONY: check
check: lint typecheck test ## Everything CI runs

.PHONY: lint
lint: ## Lint Python and TypeScript
	$(PIPELINE) ruff check src tests
	$(PIPELINE) ruff format --check src tests
	pnpm lint

.PHONY: typecheck
typecheck: ## Type-check Python and TypeScript
	$(PIPELINE) mypy src
	pnpm typecheck

.PHONY: test
test: ## Run Python and TypeScript test suites
	$(PIPELINE) pytest -q
	pnpm test

.PHONY: fmt
fmt: ## Autoformat everything
	$(PIPELINE) ruff format src tests
	$(PIPELINE) ruff check --fix src tests
	pnpm format

# ---------------------------------------------------------------- misc

.PHONY: clean
clean: ## Remove build output (keeps the data lake)
	rm -rf core/dist mcp-server/dist api/dist console/.next
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +

.PHONY: clean-data
clean-data: ## Delete the data lake. Destructive.
	rm -rf data/lake data/gaia.duckdb
