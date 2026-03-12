# -----------------------------------------------------------------------------
# Jenkins Performance Analyzer -- Makefile (Linux / macOS)
# -----------------------------------------------------------------------------

COMPOSE      = docker compose
COMPOSE_OLLAMA = docker compose --profile ollama
COMPOSE_GPU  = docker compose -f docker-compose.yml -f docker-compose.gpu.yml --profile gpu
COMPOSE_ISOLATED_OLLAMA = docker compose -f docker-compose.yml -f docker-compose.isolated.yml --profile ollama
COMPOSE_ISOLATED_GPU    = docker compose -f docker-compose.yml -f docker-compose.gpu.yml -f docker-compose.isolated.yml --profile gpu

.PHONY: help setup up up-ollama up-gpu down down-ollama down-gpu \
        up-ollama-isolated up-gpu-isolated down-isolated \
        build logs logs-api logs-frontend logs-ollama restart shell-api ps health \
        pull-model clean nuke

# -- Default target ----------------------------------------------------------
help:
	@echo ""
	@echo "  Jenkins Performance Analyzer"
	@echo "  ----------------------------"
	@echo "  make setup           copy .env.example -> .env (first-time setup)"
	@echo ""
	@echo "  Cloud AI mode (any OS, no local model needed):"
	@echo "  make up              build + start API + frontend"
	@echo ""
	@echo "  Private-only mode (block public cloud, keep on-prem/private accessible):"
	@echo "  make up-ollama-isolated  -- Ollama CPU, public cloud blocked"
	@echo "  make up-gpu-isolated     -- Ollama GPU, public cloud blocked"
	@echo "  make down-isolated       -- stop private-only stack"
	@echo "  make down            stop"
	@echo ""
	@echo "  Local Ollama, CPU mode (Mac, Linux, Windows without NVIDIA):"
	@echo "  make up-ollama       build + start API + frontend + Ollama on CPU"
	@echo "  make down-ollama     stop"
	@echo ""
	@echo "  Local Ollama, GPU mode (Linux / Windows with NVIDIA GPU):"
	@echo "  make up-gpu          build + start API + frontend + Ollama on GPU"
	@echo "  make down-gpu        stop GPU stack"
	@echo ""
	@echo "  Utilities:"
	@echo "  make logs            tail all logs"
	@echo "  make logs-api        tail API logs only"
	@echo "  make ps              show container status"
	@echo "  make health          check service health endpoints"
	@echo "  make shell-api       open shell in API container"
	@echo "  make pull-model      pull/update Ollama model"
	@echo "  make clean           remove local images"
	@echo "  make nuke            remove containers, images, volumes"
	@echo ""

# -- Setup -------------------------------------------------------------------
setup:
	@if [ ! -f .env ]; then \
		cp .env.example .env; \
		echo "[OK]  .env created -- edit it and add your API keys."; \
	else \
		echo "[!]   .env already exists -- skipping."; \
	fi

# -- Cloud AI (no Ollama) ----------------------------------------------------
up: setup
	$(COMPOSE) up --build -d
	@echo ""
	@echo "[OK]  Stack running:"
	@echo "    Frontend  -> http://localhost:$$(grep FRONTEND_PORT .env 2>/dev/null | cut -d= -f2 || echo 3000)"
	@echo "    API docs  -> http://localhost:$$(grep API_PORT .env 2>/dev/null | cut -d= -f2 || echo 8000)/docs"

down:
	$(COMPOSE) down

# -- Local Ollama, CPU mode --------------------------------------------------
up-ollama: setup
	@echo "[>]  Starting Ollama CPU stack (works on Mac, Linux, Windows)..."
	@grep -q "^AI_PROVIDER=" .env 2>/dev/null && \
		sed -i.bak 's/^AI_PROVIDER=.*/AI_PROVIDER=ollama/' .env || \
		echo "AI_PROVIDER=ollama" >> .env
	$(COMPOSE_OLLAMA) up --build -d
	@echo ""
	@echo "[OK]  Ollama CPU stack running:"
	@echo "    Frontend  -> http://localhost:$$(grep FRONTEND_PORT .env 2>/dev/null | cut -d= -f2 || echo 3000)"
	@echo "    API docs  -> http://localhost:$$(grep API_PORT .env 2>/dev/null | cut -d= -f2 || echo 8000)/docs"
	@echo "    Ollama    -> http://localhost:$$(grep OLLAMA_PORT .env 2>/dev/null | cut -d= -f2 || echo 11434)"
	@echo "    Model     -> $$(grep OLLAMA_MODEL .env 2>/dev/null | cut -d= -f2 || echo codellama:13b) (being pulled)"

down-ollama:
	$(COMPOSE_OLLAMA) down

# -- Local Ollama, GPU mode --------------------------------------------------
up-gpu: setup
	@echo "[>]  Starting Ollama GPU stack (NVIDIA required)..."
	$(COMPOSE_GPU) up --build -d
	@echo ""
	@echo "[OK]  GPU stack running:"
	@echo "    Frontend  -> http://localhost:$$(grep FRONTEND_PORT .env 2>/dev/null | cut -d= -f2 || echo 3000)"
	@echo "    API docs  -> http://localhost:$$(grep API_PORT .env 2>/dev/null | cut -d= -f2 || echo 8000)/docs"
	@echo "    Ollama    -> http://localhost:$$(grep OLLAMA_PORT .env 2>/dev/null | cut -d= -f2 || echo 11434)"
	@echo "    Model     -> $$(grep OLLAMA_MODEL .env 2>/dev/null | cut -d= -f2 || echo codellama:13b) (being pulled)"

down-gpu:
	$(COMPOSE_GPU) down

# -- Isolated mode (no internet) ---------------------------------------------
up-ollama-isolated: setup
	@echo "[>]  Starting private-only Ollama CPU stack (public cloud blocked)..."
	$(COMPOSE_ISOLATED_OLLAMA) up --build -d
	@echo ""
	@echo "[OK]  Private-only stack running (public cloud blocked):"
	@echo "    Frontend  -> http://localhost:$$(grep FRONTEND_PORT .env 2>/dev/null | cut -d= -f2 || echo 3000)"
	@echo "    API docs  -> http://localhost:$$(grep API_PORT .env 2>/dev/null | cut -d= -f2 || echo 8000)/docs"
	@echo "    Ollama    -> http://localhost:$$(grep OLLAMA_PORT .env 2>/dev/null | cut -d= -f2 || echo 11434)"

up-gpu-isolated: setup
	@echo "[>]  Starting private-only Ollama GPU stack (public cloud blocked)..."
	$(COMPOSE_ISOLATED_GPU) up --build -d
	@echo ""
	@echo "[OK]  Private-only GPU stack running (public cloud blocked):"
	@echo "    Frontend  -> http://localhost:$$(grep FRONTEND_PORT .env 2>/dev/null | cut -d= -f2 || echo 3000)"
	@echo "    API docs  -> http://localhost:$$(grep API_PORT .env 2>/dev/null | cut -d= -f2 || echo 8000)/docs"
	@echo "    Ollama    -> http://localhost:$$(grep OLLAMA_PORT .env 2>/dev/null | cut -d= -f2 || echo 11434)"

down-isolated:
	$(COMPOSE_ISOLATED_OLLAMA) down 2>/dev/null || true
	$(COMPOSE_ISOLATED_GPU) down 2>/dev/null || true

# -- Logs --------------------------------------------------------------------
logs:
	$(COMPOSE) logs -f

logs-api:
	$(COMPOSE) logs -f api

logs-frontend:
	$(COMPOSE) logs -f frontend

logs-ollama:
	$(COMPOSE_OLLAMA) logs -f ollama

# -- Operations --------------------------------------------------------------
ps:
	$(COMPOSE) ps

restart:
	$(COMPOSE) restart

health:
	@echo "-- API health ------------------------------------------"
	@curl -s http://localhost:$$(grep API_PORT .env 2>/dev/null | cut -d= -f2 || echo 8000)/health | python3 -m json.tool 2>/dev/null || echo "API not responding"
	@echo ""
	@echo "-- Frontend --------------------------------------------"
	@curl -sI http://localhost:$$(grep FRONTEND_PORT .env 2>/dev/null | cut -d= -f2 || echo 3000) | head -3 || echo "Frontend not responding"

shell-api:
	$(COMPOSE) exec api /bin/bash || $(COMPOSE) exec api /bin/sh

pull-model:
	@MODEL=$$(grep OLLAMA_MODEL .env 2>/dev/null | cut -d= -f2 || echo "codellama:13b"); \
	echo "Pulling $$MODEL..."; \
	curl -X POST http://localhost:$$(grep OLLAMA_PORT .env 2>/dev/null | cut -d= -f2 || echo 11434)/api/pull \
		-H "Content-Type: application/json" \
		-d "{\"name\": \"$$MODEL\"}"

# -- Cleanup -----------------------------------------------------------------
clean:
	$(COMPOSE) down --rmi local

nuke:
	@echo "[!]  Removes ALL containers, images, and volumes."
	@read -p "     Are you sure? [y/N] " confirm && [ "$$confirm" = "y" ] || exit 1
	$(COMPOSE) down -v --rmi all
	$(COMPOSE_OLLAMA) down -v --rmi all 2>/dev/null || true
	$(COMPOSE_GPU) down -v --rmi all 2>/dev/null || true
	@echo "[OK]  All resources removed."
