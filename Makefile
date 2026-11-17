# One Verifiable Record — local deployment shortcuts.
# Full runbook: docs/deploy-local.md. Everything binds to loopback by default.
SHELL := /bin/bash
COMPOSE := docker compose
PY := python3

.DEFAULT_GOAL := help

.PHONY: help setup up up-llm up-gpu down logs ps \
        data-% import \
        embed-chunks embed-claims embed extract extract-rules pipeline align check clean-volumes

help: ## show this list
	@grep -E '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "} {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

setup: ## create .env from .env.example if missing
	@test -f .env || cp .env.example .env
	@echo ".env ready — set LLM_BASE_URL/LLM_MODEL (local model or hosted key) and EMBED_* as needed"

up: setup ## core stack: mariadb + server + web + embedder (loopback only)
	$(COMPOSE) up -d --build

up-llm: setup ## core stack + local Ollama chat model
	$(COMPOSE) --profile local-llm up -d --build
	@echo "Pull a model, then set LLM_BASE_URL=http://ollama:11434/v1 + LLM_MODEL in .env:"
	@echo "  $(COMPOSE) exec ollama ollama pull qwen3:8b"

up-gpu: setup ## core stack + local GPU embedder (NVIDIA toolkit required)
	$(COMPOSE) --profile gpu up -d --build
	@echo "Then set EMBED_BASE_URL=http://embedder-gpu:8002/v1 (+ EMBED_API_KEY) in .env"

down: ## stop all services (volumes kept)
	$(COMPOSE) down

logs: ## follow logs
	$(COMPOSE) logs -f

ps: ## list services
	$(COMPOSE) ps

data-%: ## download a dataset size: make data-small | data-medium | data-large
	./db/load/download_datasets.sh $*

import: ## import the downloaded dataset into MariaDB
	$(PY) -m pip install -r db/load/requirements.txt
	$(PY) db/load/import_dump.py --data-dir db/load/data --only-shared

embed-chunks: ## embed article chunks (RAG passages) via EMBED_BASE_URL
	$(PY) -m pip install -r jobs/requirements.txt
	$(PY) jobs/embed.py --tables chunks

embed-claims: ## embed extracted claims via EMBED_BASE_URL
	$(PY) jobs/embed.py --tables claims

embed: embed-chunks embed-claims ## embed both tables

extract: ## LLM claim extraction pilot (needs LLM_BASE_URL/LLM_MODEL in .env)
	$(PY) jobs/extract_claims.py --pilot 20

extract-rules: ## LLM-free rules-based extraction fallback
	$(PY) jobs/extract_rules.py --all

pipeline: embed-chunks extract embed-claims ## full cold path (assumes data imported)

align: ## align one page: make align PAGE=123
	curl -sS -X POST "localhost:8000/api/pages/$(PAGE)/align"

check: ## compile all Python + verify cross-module imports (no deps needed)
	$(PY) scripts/check.py

clean-volumes: ## DANGER: stop services and delete the MariaDB/ollama volumes
	$(COMPOSE) down -v
