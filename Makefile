SHELL := /bin/bash
UV ?= uv
WORKSPACE_ROOT ?= $(CURDIR)
DB_PATH ?= $(WORKSPACE_ROOT)/.anamnesis/anamnesis.db
HOST ?= 127.0.0.1
PORT ?= 8000

.PHONY: help install sync build test compile verify init smoke-clients mcp mcp-http codex-sync opencode-sync clean-dist

help: ## Show available commands
	@grep -E '^[a-zA-Z0-9_.-]+:.*?## ' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "%-16s %s\n", $$1, $$2}'

install: ## Initialize submodules and sync the uv workspace
	git submodule update --init --recursive
	$(UV) sync --all-packages --group dev

sync: install ## Alias for install

build: ## Build both uqa and anamnesis packages
	$(UV) build --all-packages

test: ## Run the unit test suite
	$(UV) run python -m unittest discover -s tests -v

compile: ## Byte-compile project sources
	$(UV) run python -m compileall anamnesis tests scripts

verify: ## Run tests, compile checks, build, and release verification
	$(UV) run python -m unittest discover -s tests -v
	$(UV) run python -m compileall anamnesis tests scripts
	$(UV) build --all-packages
	$(UV) run python scripts/verify_uv_release.py

init: ## Generate local Claude/Codex/OpenCode config for this workspace
	$(UV) run anamnesis-init --workspace-root "$(WORKSPACE_ROOT)"

smoke-clients: ## End-to-end smoke test for Claude Code, Codex, and OpenCode wiring
	$(UV) run python scripts/smoke_client_connections.py

mcp: ## Run the MCP server over stdio
	$(UV) run anamnesis-mcp

mcp-http: ## Run the MCP server over streamable HTTP
	$(UV) run anamnesis-mcp --transport streamable-http --host "$(HOST)" --port "$(PORT)"

codex-sync: ## Backfill Codex history and sessions into DB_PATH
	$(UV) run anamnesis-codex-sync --db "$(DB_PATH)"

opencode-sync: ## Backfill OpenCode sessions into DB_PATH
	$(UV) run anamnesis-opencode-sync --db "$(DB_PATH)" --all-sessions

clean-dist: ## Remove build artifacts
	rm -rf build dist *.egg-info anamnesis.egg-info agent_memory.egg-info
	rm -rf uqa/build uqa/dist uqa/*.egg-info
