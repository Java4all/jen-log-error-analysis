#!/usr/bin/env bash
# Jenkins Performance Analyzer - Mac / Linux launcher
# Usage: bash setup.sh

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

header() { echo ""; echo -e "${CYAN}${BOLD}  $*${NC}"; }
ok()     { echo -e "  ${GREEN}[OK]${NC} $*"; }
warn()   { echo -e "  ${YELLOW}[!]${NC}  $*"; }
err()    { echo -e "  ${RED}[x]${NC}  $*"; }
info()   { echo -e "       $*"; }

if ! docker info > /dev/null 2>&1; then
    err "Docker is not running. Start Docker Desktop and try again."
    exit 1
fi
if ! docker compose version > /dev/null 2>&1; then
    err "docker compose plugin not found. Update Docker Desktop to 4.x+"
    exit 1
fi

if [ ! -f .env ]; then
    cp .env.example .env
    ok ".env created from .env.example"
    warn "Edit .env and add your ANTHROPIC_API_KEY if using cloud AI."
fi

get_port() { grep "^$1=" .env 2>/dev/null | cut -d= -f2 || echo "$2"; }

header "Jenkins Performance Analyzer"
echo   "  ========================================="
echo   ""
echo   "  Choose a run mode:"
echo   ""
echo   "  --- Build from source -------------------------------------------"
echo   "  [1]  Cloud AI only  (Anthropic / private, no local model)"
echo   "       Any machine. Requires ANTHROPIC_API_KEY in .env."
echo   ""
echo   "  [2]  Local Ollama, CPU  (Mac, Linux, any machine)"
echo   "       Runs Ollama in Docker on CPU. First run pulls model."
echo   ""
echo   "  [3]  Local Ollama, GPU  (Linux / Windows NVIDIA GPU)"
echo   "       Fast local inference with NVIDIA GPU."
echo   ""
echo   "  [4]  ISOLATED, CPU  (air-gapped / no internet)"
echo   "       Ollama CPU + Docker internal network + ISOLATED_MODE=true."
echo   ""
echo   "  [5]  ISOLATED, GPU  (air-gapped + NVIDIA GPU)"
echo   "       Ollama GPU + Docker internal network + ISOLATED_MODE=true."
echo   ""
echo   "  --- Pre-built images (no build required) ------------------------"
echo   "  [6]  Pre-built, cloud AI     (IMAGE_REGISTRY in .env required)"
echo   "  [7]  Pre-built, Ollama CPU   (IMAGE_REGISTRY in .env required)"
echo   "  [8]  Pre-built, host Ollama  (IMAGE_REGISTRY in .env required)"
echo   "       Pulls images from registry + uses Ollama on your Mac."
echo   "  [9]  Pre-built, host Ollama  (no registry -- local images only)"
echo   "       Uses images already on this machine + Ollama on your Mac."
echo   "       Run after: make push-images  or  .\\make.ps1 push-images"
echo   "  [10] Pre-built, isolated     (IMAGE_REGISTRY in .env required)"
echo   ""
echo   "  --- Utilities ---------------------------------------------------"
echo   "  [11] Stop all containers"
echo   "  [12] Show container status"
echo   "  [13] Tail logs"
echo   "  [14] Open app in browser"
echo   "  [q]  Quit"
echo   ""
read -p "  Enter choice: " CHOICE

case "$CHOICE" in
    1)
        header "Starting cloud AI mode..."
        make up
        FP=$(get_port FRONTEND_PORT 3000)
        echo ""; ok "Open: http://localhost:$FP"
        ;;
    2)
        header "Starting local Ollama (CPU)..."
        warn "First run downloads the model -- may take several minutes."
        info "Tip: set OLLAMA_MODEL=phi3:mini in .env for faster CPU inference on Mac."
        make up-ollama
        FP=$(get_port FRONTEND_PORT 3000)
        echo ""; ok "Open: http://localhost:$FP"
        ;;
    3)
        header "Starting Ollama GPU mode (NVIDIA required)..."
        if command -v nvidia-smi &>/dev/null; then
            GPU=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)
            ok "GPU: $GPU"
        else
            warn "nvidia-smi not found -- make sure NVIDIA drivers are installed."
        fi
        make up-gpu
        FP=$(get_port FRONTEND_PORT 3000)
        echo ""; ok "Open: http://localhost:$FP"
        ;;
    4)
        header "Starting ISOLATED mode (CPU, no internet)..."
        warn "Internet access blocked at both application and Docker network layer."
        info "AI provider will be forced to ollama. Anthropic/public GitHub blocked."
        make up-ollama-isolated
        FP=$(get_port FRONTEND_PORT 3000)
        echo ""; ok "Open: http://localhost:$FP"
        ;;
    5)
        header "Starting ISOLATED mode (GPU, no internet)..."
        warn "Internet access blocked at both application and Docker network layer."
        make up-gpu-isolated
        FP=$(get_port FRONTEND_PORT 3000)
        echo ""; ok "Open: http://localhost:$FP"
        ;;
    6)
        header "Starting pre-built stack (cloud AI)..."
        info "Pulls images from IMAGE_REGISTRY in .env -- no build required."
        REG=$(get_port IMAGE_REGISTRY "")
        if [ -z "$REG" ]; then
            warn "IMAGE_REGISTRY is not set in .env -- set it before running this mode."
            exit 1
        fi
        make up-prebuilt
        FP=$(get_port FRONTEND_PORT 3000)
        echo ""; ok "Open: http://localhost:$FP"
        ;;
    7)
        header "Starting pre-built stack (Ollama CPU)..."
        info "Pulls images from IMAGE_REGISTRY in .env -- no build required."
        REG=$(get_port IMAGE_REGISTRY "")
        if [ -z "$REG" ]; then
            warn "IMAGE_REGISTRY is not set in .env -- set it before running this mode."
            exit 1
        fi
        warn "First run downloads the Ollama model -- may take several minutes."
        make up-prebuilt-ollama
        FP=$(get_port FRONTEND_PORT 3000)
        echo ""; ok "Open: http://localhost:$FP"
        ;;
    8)
        header "Starting pre-built stack (host Ollama, registry required)..."
        info "Pulls images from IMAGE_REGISTRY in .env -- no build, no Docker Ollama."
        REG=$(get_port IMAGE_REGISTRY "")
        if [ -z "$REG" ]; then
            warn "IMAGE_REGISTRY is not set in .env -- set it before running this mode."
            exit 1
        fi
        OLLAMA_PORT_VAL=$(get_port OLLAMA_PORT 11434)
        if curl -sf --max-time 3 http://localhost:$OLLAMA_PORT_VAL/api/tags > /dev/null 2>&1; then
            ok "Host Ollama is running"
        else
            warn "Ollama not detected -- start with: OLLAMA_HOST=0.0.0.0 ollama serve"
        fi
        make up-prebuilt-host-ollama
        FP=$(get_port FRONTEND_PORT 3000)
        echo ""; ok "Open: http://localhost:$FP"
        ;;
    9)
        header "Starting pre-built local images with host Ollama (no registry)..."
        info "Uses images already on this machine + Ollama running on your Mac."
        info "No docker build. No IMAGE_REGISTRY required."
        echo ""
        OLLAMA_PORT_VAL=$(get_port OLLAMA_PORT 11434)
        if curl -sf --max-time 3 http://localhost:$OLLAMA_PORT_VAL/api/tags > /dev/null 2>&1; then
            ok "Host Ollama is running on port $OLLAMA_PORT_VAL"
            MODELS=$(curl -sf http://localhost:$OLLAMA_PORT_VAL/api/tags 2>/dev/null \
              | python3 -c "import sys,json; d=json.load(sys.stdin); print(', '.join(m['name'] for m in d.get('models',[])))" \
              2>/dev/null || echo "unknown")
            info "Available models: $MODELS"
        else
            warn "Ollama not detected -- start with: OLLAMA_HOST=0.0.0.0 ollama serve"
        fi
        docker compose -f docker-compose.mac-ollama.yml up -d
        FP=$(get_port FRONTEND_PORT 3000)
        echo ""; ok "Open: http://localhost:$FP"
        ;;
    10)
        header "Starting pre-built isolated stack (Ollama CPU, private-only)..."
        info "Pulls images from IMAGE_REGISTRY in .env -- no build required."
        info "Public cloud (Anthropic + github.com) is blocked."
        REG=$(get_port IMAGE_REGISTRY "")
        if [ -z "$REG" ]; then
            warn "IMAGE_REGISTRY is not set in .env -- set it before running this mode."
            exit 1
        fi
        make up-prebuilt-isolated
        FP=$(get_port FRONTEND_PORT 3000)
        echo ""; ok "Open: http://localhost:$FP"
        ;;
    11)
        header "Stopping all containers..."
        docker compose down
        docker compose --profile ollama down 2>/dev/null || true
        docker compose -f docker-compose.mac-ollama.yml down 2>/dev/null || true
        make down-isolated 2>/dev/null || true
        ok "Done."
        ;;
    12)
        header "Container status"
        docker compose ps
        ;;
    13)
        header "Tailing logs (Ctrl+C to stop)..."
        docker compose logs -f
        ;;
    14)
        FP=$(get_port FRONTEND_PORT 3000)
        URL="http://localhost:$FP"
        header "Opening $URL"
        if command -v open &>/dev/null; then open "$URL"
        elif command -v xdg-open &>/dev/null; then xdg-open "$URL"
        else info "Navigate to: $URL"; fi
        ;;
    q|Q) echo "" ;;
    *)   err "Unknown choice: $CHOICE"; exit 1 ;;
esac
