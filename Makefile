COMPOSE = docker compose
INJECT = ./scripts/inject-secrets.sh

# On Windows use scripts/inject-secrets.ps1 directly

.PHONY: help setup-secrets up up-fg down reset reset-all build logs shell health test

.DEFAULT_GOAL := help

help: ## Show this help message
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m<target>\033[0m\n"} /^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2 } /^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) } ' $(MAKEFILE_LIST)

setup-secrets: ## Run secret setup script and create data directories
	@mkdir -p whatsapp-data/session whatsapp-data/messages whatsapp-data/db
	@chmod +x scripts/setup-secrets.sh
	@./scripts/setup-secrets.sh

up: ## Start services in background with secret injection
	@chmod +x $(INJECT)
	$(INJECT) $(COMPOSE) up -d

up-fg: ## Start services in foreground with secret injection
	@chmod +x $(INJECT)
	$(INJECT) $(COMPOSE) up

down: ## Stop and remove containers
	$(COMPOSE) down

reset: ## Stop containers and remove database data (preserves session/messages)
	$(COMPOSE) down -v
	rm -rf whatsapp-data/db/

reset-all: ## Stop containers and remove ALL data with confirmation
	@echo "WARNING: This will delete all WhatsApp sessions and messages."
	@read -p "Are you sure? [y/N] " ans && [ $${ans:-N} = y ]
	$(COMPOSE) down -v
	rm -rf whatsapp-data/

build: ## Build images without cache
	$(COMPOSE) build --no-cache

logs: ## Tail container logs
	$(COMPOSE) logs -f

shell: ## Open a shell in the running app container
	$(COMPOSE) exec app /bin/bash

health: ## Check application health status
	@curl -s http://localhost:8080/health | python3 -m json.tool

test: ## Run test suite inside the container
	$(COMPOSE) run --rm app python -m pytest tests/