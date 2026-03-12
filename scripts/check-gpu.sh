#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# scripts/check-gpu.sh -- Verify NVIDIA Docker prerequisites before running
# Usage: bash scripts/check-gpu.sh
# -----------------------------------------------------------------------------

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
PASS="${GREEN}[OK] PASS${NC}"; FAIL="${RED}[x] FAIL${NC}"; WARN="${YELLOW}[!]  WARN${NC}"

echo ""
echo "  Jenkins Analyzer -- GPU Prerequisite Check"
echo "  ------------------------------------------"
echo ""

ERRORS=0

# 1. nvidia-smi
printf "  %-40s " "nvidia-smi available"
if command -v nvidia-smi &>/dev/null; then
    GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 || echo "unknown")
    DRIVER=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -1 || echo "?")
    VRAM=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1 || echo "?")
    echo -e "$PASS  ($GPU_NAME, driver $DRIVER, ${VRAM}MiB VRAM)"
else
    echo -e "$FAIL -- nvidia-smi not found. Install NVIDIA drivers."
    ERRORS=$((ERRORS + 1))
fi

# 2. Docker
printf "  %-40s " "Docker installed"
if command -v docker &>/dev/null; then
    DOCKER_VER=$(docker --version | awk '{print $3}' | tr -d ',')
    echo -e "$PASS  (v$DOCKER_VER)"
else
    echo -e "$FAIL -- docker not found."
    ERRORS=$((ERRORS + 1))
fi

# 3. Docker Compose v2
printf "  %-40s " "Docker Compose v2"
if docker compose version &>/dev/null; then
    COMPOSE_VER=$(docker compose version --short 2>/dev/null || echo "?")
    echo -e "$PASS  (v$COMPOSE_VER)"
else
    echo -e "$FAIL -- 'docker compose' plugin not found. Install Docker Compose v2."
    ERRORS=$((ERRORS + 1))
fi

# 4. NVIDIA Container Toolkit
printf "  %-40s " "nvidia-container-toolkit"
if dpkg -l nvidia-container-toolkit &>/dev/null 2>&1 || \
   rpm -q nvidia-container-toolkit &>/dev/null 2>&1 || \
   command -v nvidia-container-cli &>/dev/null; then
    echo -e "$PASS"
else
    echo -e "$FAIL -- not installed."
    echo "         Install: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html"
    ERRORS=$((ERRORS + 1))
fi

# 5. Docker nvidia runtime configured
printf "  %-40s " "nvidia runtime in Docker"
if docker info 2>/dev/null | grep -q nvidia; then
    echo -e "$PASS"
else
    echo -e "$WARN -- nvidia runtime not listed in 'docker info'."
    echo "         Run: sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker"
fi

# 6. Quick GPU container test
printf "  %-40s " "GPU accessible in container"
if docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi -L &>/dev/null 2>&1; then
    echo -e "$PASS"
else
    echo -e "$WARN -- could not run GPU container test. Docker may need a restart."
fi

# 7. VRAM estimate
echo ""
echo "  VRAM Recommendations:"
echo "  ---------------------"
if command -v nvidia-smi &>/dev/null; then
    VRAM_MB=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | awk '{sum+=$1} END{print sum}')
    VRAM_GB=$(echo "scale=1; $VRAM_MB / 1024" | bc 2>/dev/null || echo "?")
    echo "  Detected VRAM: ~${VRAM_GB} GB"
    echo ""
    if [ "$VRAM_MB" -ge 24000 ] 2>/dev/null; then
        echo -e "  ${GREEN}-> codellama:13b or llama3:13b  (recommended for best quality)${NC}"
    elif [ "$VRAM_MB" -ge 8000 ] 2>/dev/null; then
        echo -e "  ${YELLOW}-> codellama:7b or mistral:7b   (fits in 8GB VRAM)${NC}"
    else
        echo -e "  ${RED}-> codellama:7b-q4 or phi3:mini  (low VRAM -- use quantized models)${NC}"
    fi
fi

echo ""
if [ "$ERRORS" -eq 0 ]; then
    echo -e "  ${GREEN}All prerequisites met. Run: make up-gpu${NC}"
else
    echo -e "  ${RED}$ERRORS prerequisite(s) failed. Fix the issues above before running GPU mode.${NC}"
fi
echo ""
