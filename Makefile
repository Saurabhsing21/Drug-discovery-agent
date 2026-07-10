.PHONY: help bootstrap test test-unit test-integration lint typecheck quality api-dev frontend-install frontend-build docker-build docker-up docker-down docker-logs deploy-up deploy-down deploy-logs

help:
	@echo "Targets:"
	@echo "  bootstrap        Create venv + install deps"
	@echo "  test             Run full pytest suite"
	@echo "  test-unit        Run unit tests only (tests/unit/)"
	@echo "  test-integration Run integration tests only"
	@echo "  lint             Ruff check on src/ api/ cli/"
	@echo "  typecheck        mypy on src/drugagent"
	@echo "  quality          Ruff + mypy + coverage gates"
	@echo "  api-dev          Start FastAPI dev server (api.main:app)"
	@echo "  frontend-install Install frontend deps"
	@echo "  frontend-build   Build Next.js frontend"
	@echo "  docker-build     Build production docker images"
	@echo "  docker-up        Start prod stack (nginx+api+web)"
	@echo "  docker-down      Stop stack"
	@echo "  docker-logs      Tail stack logs"
	@echo "  deploy-up        Start stack via deploy/ (recommended)"
	@echo "  deploy-down      Stop deploy/ stack"
	@echo "  deploy-logs      Tail deploy/ stack logs"

bootstrap:
	./scripts/bootstrap_dev.sh

test:
	PYTHONPATH=src python3 -m pytest tests/ -q

test-unit:
	PYTHONPATH=src python3 -m pytest tests/ -q --ignore=tests/test_connector_integrations.py --ignore=tests/test_mcp_endpoints.py --ignore=tests/test_saved_runs_api.py --ignore=tests/test_server_manager.py

test-integration:
	PYTHONPATH=src python3 -m pytest tests/test_connector_integrations.py tests/test_mcp_endpoints.py tests/test_saved_runs_api.py tests/test_server_manager.py -v

lint:
	python3 -m ruff check src/ api/ cli/ interfaces/

typecheck:
	python3 -m mypy src/drugagent --ignore-missing-imports

quality:
	./scripts/ci_quality_gates.sh

api-dev:
	PYTHONPATH=src uvicorn api.main:app --reload --port 8000

frontend-install:
	cd frontend && npm ci

frontend-build:
	cd frontend && npm run build

docker-build:
	docker compose build

docker-up:
	docker compose up -d --build

docker-down:
	docker compose down

docker-logs:
	docker compose logs -f --tail=200

deploy-up:
	cd deploy && cp -n .env.example .env && docker compose up -d --build

deploy-down:
	cd deploy && docker compose down

deploy-logs:
	cd deploy && docker compose logs -f --tail=200
