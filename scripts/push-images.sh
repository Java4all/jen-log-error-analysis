#!/usr/bin/env bash
# push-images.sh -- Build multi-architecture images and push to a private registry.
#
# Builds for linux/amd64 AND linux/arm64 in a single pass using docker buildx.
# The resulting manifest list means one image tag works on any architecture --
# x86 servers, Apple Silicon Macs, Raspberry Pi, AWS Graviton, etc.
#
# Prerequisites (first time only):
#   bash scripts/push-images.sh --setup        create a multi-arch buildx builder
#
# Usage:
#   bash scripts/push-images.sh [options] [tag]
#
# Options:
#   --setup          Create/activate the multi-arch buildx builder then exit
#   --amd64-only     Build for linux/amd64 only
#   --arm64-only     Build for linux/arm64 only
#   --no-cache       Pass --no-cache to docker buildx
#   --tag <tag>      Image tag  (default: value of IMAGE_TAG or "latest")
#
# Environment (.env or shell):
#   IMAGE_REGISTRY   e.g. registry.mycompany.com   (empty = local tar export)
#   IMAGE_REPO       e.g. devops/jenkins-analyzer  (default: jenkins-analyzer)
#   IMAGE_TAG        e.g. v1.2                      (default: latest)

set -euo pipefail
cd "$(dirname "$0")/.."

# Load .env -- only set vars that are not already set in the environment
# (set -a; source .env would overwrite existing shell vars -- we don't want that)
if [ -f .env ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    # Skip blank lines and comments
    [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
    # Match KEY=VALUE (no spaces around =)
    if [[ "$line" =~ ^([^=[:space:]]+)=(.*)$ ]]; then
      key="${BASH_REMATCH[1]}"
      val="${BASH_REMATCH[2]}"
      # Strip surrounding quotes
      val="${val%"}"
      val="${val#"}"
      val="${val%'}"
      val="${val#'}"
      # Only export if not already set in the environment
      if [[ -z "${!key+x}" ]]; then
        export "$key=$val"
      fi
    fi
  done < .env
fi

# -- Parse args ----------------------------------------------------------------
SETUP=false
NO_CACHE=""
PLATFORMS="linux/amd64,linux/arm64"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --setup)        SETUP=true; shift ;;
    --amd64-only)   PLATFORMS="linux/amd64"; shift ;;
    --arm64-only)   PLATFORMS="linux/arm64"; shift ;;
    --no-cache)     NO_CACHE="--no-cache"; shift ;;
    --tag)          IMAGE_TAG="${2}"; shift 2 ;;
    *)              IMAGE_TAG="${1}"; shift ;;
  esac
done

IMAGE_REGISTRY="${IMAGE_REGISTRY:-}"
IMAGE_REPO="${IMAGE_REPO:-jenkins-analyzer}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
BUILDER_NAME="jenkins-analyzer-builder"

echo "  [debug] Registry='${IMAGE_REGISTRY:-<empty>}'  Repo='${IMAGE_REPO}'  Tag='${IMAGE_TAG}'"

# -- Setup: create multi-arch builder ------------------------------------------
setup_builder() {
  echo ""
  echo "=== Setting up multi-arch buildx builder ==="
  if docker buildx inspect "$BUILDER_NAME" &>/dev/null; then
    echo "  Builder '$BUILDER_NAME' already exists -- activating."
    docker buildx use "$BUILDER_NAME"
  else
    echo "  Creating builder '$BUILDER_NAME' with docker-container driver..."
    docker buildx create \
      --name "$BUILDER_NAME" \
      --driver docker-container \
      --driver-opt network=host \
      --bootstrap \
      --use
    echo "  Verifying platform support..."
    docker buildx inspect --bootstrap | grep -E "Platforms|Name"
  fi
  echo ""
  echo "[OK] Builder ready. Supported platforms:"
  docker buildx inspect "$BUILDER_NAME" | grep "Platforms"
  echo ""
}

if [ "$SETUP" = "true" ]; then
  setup_builder
  exit 0
fi

# Ensure a capable builder is active
if ! docker buildx inspect "$BUILDER_NAME" &>/dev/null; then
  echo "[!] Multi-arch builder not found. Running setup first..."
  setup_builder
else
  docker buildx use "$BUILDER_NAME" 2>/dev/null || true
fi

PUSH=false
[ -n "$IMAGE_REGISTRY" ] && PUSH=true

prefix="${IMAGE_REGISTRY:+${IMAGE_REGISTRY}/}${IMAGE_REPO}"
api_image="${prefix}/api:${IMAGE_TAG}"
fe_image="${prefix}/frontend:${IMAGE_TAG}"

echo ""
echo "=== Jenkins Analyzer -- Multi-Arch Build & Push ==="
echo "  Platforms : ${PLATFORMS}"
echo "  Registry  : ${IMAGE_REGISTRY:-<local tar export>}"
echo "  Repo      : ${IMAGE_REPO}"
echo "  Tag       : ${IMAGE_TAG}"
echo "  API       : ${api_image}"
echo "  Frontend  : ${fe_image}"
echo ""

if [ "$PUSH" = "true" ]; then
  # -- Registry push: single manifest covers all platforms ------------------
  echo "[1/2] Building + pushing API image (${PLATFORMS})..."
  docker buildx build \
    --platform "${PLATFORMS}" \
    --tag "${api_image}" \
    --tag "${prefix}/api:latest" \
    ${NO_CACHE} \
    --push \
    ./backend

  echo "[2/2] Building + pushing frontend image (${PLATFORMS})..."
  docker buildx build \
    --platform "${PLATFORMS}" \
    --tag "${fe_image}" \
    --tag "${prefix}/frontend:latest" \
    --build-arg VITE_API_URL="" \
    ${NO_CACHE} \
    --push \
    -f frontend/Dockerfile .

  echo ""
  echo "[OK] Multi-arch images pushed."
  echo "     On any host (x86, ARM, Apple Silicon) just run:"
  echo "       make up-prebuilt            or"
  echo "       docker compose -f docker-compose.yml -f docker-compose.prebuilt.yml up"
  echo ""
  echo "     .env for restricted hosts:"
  echo "       IMAGE_REGISTRY=${IMAGE_REGISTRY}"
  echo "       IMAGE_REPO=${IMAGE_REPO}"
  echo "       IMAGE_TAG=${IMAGE_TAG}"

else
  # -- No registry: export one tar.gz per platform ---------------------------
  # docker save does not support multi-arch tarballs; save each arch separately.
  mkdir -p dist

  for PLATFORM in ${PLATFORMS//,/ }; do
    ARCH="${PLATFORM#linux/}"   # amd64 | arm64
    echo "[>] Building + exporting API   [${PLATFORM}]..."
    docker buildx build \
      --platform "${PLATFORM}" \
      --tag "${IMAGE_REPO}/api:${IMAGE_TAG}-${ARCH}" \
      ${NO_CACHE} \
      --load \
      ./backend
    docker save "${IMAGE_REPO}/api:${IMAGE_TAG}-${ARCH}" \
      | gzip > "dist/jenkins-analyzer-api-${IMAGE_TAG}-${ARCH}.tar.gz"

    echo "[>] Building + exporting frontend [${PLATFORM}]..."
    docker buildx build \
      --platform "${PLATFORM}" \
      --tag "${IMAGE_REPO}/frontend:${IMAGE_TAG}-${ARCH}" \
      --build-arg VITE_API_URL="" \
      ${NO_CACHE} \
      --load \
      -f frontend/Dockerfile .
    docker save "${IMAGE_REPO}/frontend:${IMAGE_TAG}-${ARCH}" \
      | gzip > "dist/jenkins-analyzer-frontend-${IMAGE_TAG}-${ARCH}.tar.gz"
  done

  echo ""
  echo "[OK] Archives saved to ./dist/:"
  ls -lh dist/jenkins-analyzer-*-${IMAGE_TAG}-*.tar.gz 2>/dev/null || ls -lh dist/
  echo ""
  echo "On the target host, load the archive matching its architecture:"
  echo "  x86 / AMD64 server:"
  echo "    docker load < dist/jenkins-analyzer-api-${IMAGE_TAG}-amd64.tar.gz"
  echo "    docker load < dist/jenkins-analyzer-frontend-${IMAGE_TAG}-amd64.tar.gz"
  echo "  ARM64 / Apple Silicon / Graviton:"
  echo "    docker load < dist/jenkins-analyzer-api-${IMAGE_TAG}-arm64.tar.gz"
  echo "    docker load < dist/jenkins-analyzer-frontend-${IMAGE_TAG}-arm64.tar.gz"
  echo ""
  echo "After loading, set IMAGE_TAG=${IMAGE_TAG}-<arch> in .env, then:"
  echo "  make up-prebuilt"
fi
