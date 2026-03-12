# push-images.ps1 -- Build multi-architecture images and push to a private registry.
#
# Builds for linux/amd64 AND linux/arm64 in a single pass using docker buildx.
# The resulting manifest list means one image tag works on any architecture:
# x86 servers, Apple Silicon Macs, AWS Graviton, Raspberry Pi, etc.
#
# Prerequisites (first time only):
#   .\scripts\push-images.ps1 -Setup
#
# Usage:
#   .\scripts\push-images.ps1 [-Tag v1.2] [-Registry registry.mycompany.com]
#   .\scripts\push-images.ps1 -Amd64Only
#   .\scripts\push-images.ps1 -Arm64Only
#   .\scripts\push-images.ps1 -Setup
[CmdletBinding()]
param(
  [string]$Registry  = "",
  [string]$Repo      = "",
  [string]$Tag       = "",
  [switch]$Setup,
  [switch]$Amd64Only,
  [switch]$Arm64Only,
  [switch]$NoCache
)

# Use Continue -- we check $LASTEXITCODE manually after each docker call.
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

# ---------------------------------------------------------------------------
# Resolve Registry / Repo / Tag:
#   1. Use -Registry / -Repo / -Tag params if provided (passed by make.ps1)
#   2. Fall back to environment variables
#   3. Fall back to .env file values
#   4. Use built-in defaults
# ---------------------------------------------------------------------------
function Read-DotEnv {
  $map = @{}
  if (Test-Path ".env") {
    Get-Content ".env" | ForEach-Object {
      if ($_ -match "^\s*([^#=][^=]*)=(.*)$") {
        $map[$Matches[1].Trim()] = $Matches[2].Trim().Trim('"').Trim("'")
      }
    }
  }
  return $map
}
$dotenv = Read-DotEnv

if (-not $Registry) { $Registry = if ($env:IMAGE_REGISTRY) { $env:IMAGE_REGISTRY } elseif ($dotenv["IMAGE_REGISTRY"]) { $dotenv["IMAGE_REGISTRY"] } else { "" } }
if (-not $Repo)     { $Repo     = if ($env:IMAGE_REPO)     { $env:IMAGE_REPO }     elseif ($dotenv["IMAGE_REPO"])     { $dotenv["IMAGE_REPO"] }     else { "jenkins-analyzer" } }
if (-not $Tag)      { $Tag      = if ($env:IMAGE_TAG)      { $env:IMAGE_TAG }      elseif ($dotenv["IMAGE_TAG"])      { $dotenv["IMAGE_TAG"] }      else { "latest" } }

Write-Host "  [debug] Registry='$Registry'  Repo='$Repo'  Tag='$Tag'" -ForegroundColor DarkGray

$BuilderName = "jenkins-analyzer-builder"
$Platforms   = if ($Amd64Only) { "linux/amd64" } elseif ($Arm64Only) { "linux/arm64" } else { "linux/amd64,linux/arm64" }
$NoC         = if ($NoCache) { "--no-cache" } else { "" }

# ---------------------------------------------------------------------------
# Helper: print and run a command string, abort on failure
# Avoids array-splatting to external programs which breaks in PS5
# ---------------------------------------------------------------------------
function Run([string]$Cmd, [string]$ErrMsg) {
  Write-Host "  > $Cmd" -ForegroundColor DarkGray
  Invoke-Expression $Cmd
  if ($LASTEXITCODE -ne 0) {
    Write-Host "[FAIL] $ErrMsg (exit $LASTEXITCODE)" -ForegroundColor Red
    exit $LASTEXITCODE
  }
}

# ---------------------------------------------------------------------------
# Setup: create the multi-arch buildx builder
# ---------------------------------------------------------------------------
function Setup-Builder {
  Write-Host ""
  Write-Host "=== Setting up multi-arch buildx builder ===" -ForegroundColor Cyan

  $ErrorActionPreference = "SilentlyContinue"
  docker buildx inspect $BuilderName 2>$null | Out-Null
  $exists = ($LASTEXITCODE -eq 0)
  $ErrorActionPreference = "Continue"

  if ($exists) {
    Write-Host "  Builder '$BuilderName' already exists -- activating."
    docker buildx use $BuilderName 2>$null | Out-Null
  } else {
    Write-Host "  Creating builder '$BuilderName'..."
    # --driver-opt network=host is Linux-only; omitted for Windows Docker Desktop
    Run "docker buildx create --name $BuilderName --driver docker-container --bootstrap --use" `
        "Failed to create buildx builder"
  }

  Write-Host ""
  Write-Host "[OK] Builder ready." -ForegroundColor Green
  docker buildx inspect $BuilderName | Select-String "Platforms"
  Write-Host ""
}

if ($Setup) { Setup-Builder; exit 0 }

# ---------------------------------------------------------------------------
# Ensure builder exists (auto-creates if missing)
# ---------------------------------------------------------------------------
$ErrorActionPreference = "SilentlyContinue"
docker buildx inspect $BuilderName 2>$null | Out-Null
$builderExists = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = "Continue"

if (-not $builderExists) {
  Write-Warning "Builder '$BuilderName' not found -- running setup first."
  Setup-Builder
} else {
  docker buildx use $BuilderName 2>$null | Out-Null
}

# ---------------------------------------------------------------------------
# Print plan
# ---------------------------------------------------------------------------
$Push   = ($Registry -ne "")
$Prefix = if ($Registry) { "$Registry/$Repo" } else { $Repo }
$ApiImg = "${Prefix}/api:${Tag}"
$FeImg  = "${Prefix}/frontend:${Tag}"

Write-Host ""
Write-Host "=== Jenkins Analyzer -- Multi-Arch Build & Push ===" -ForegroundColor Cyan
Write-Host "  Platforms : $Platforms"
Write-Host "  Registry  : $(if ($Registry) { $Registry } else { '<local tar export>' })"
Write-Host "  Repo      : $Repo"
Write-Host "  Tag       : $Tag"
Write-Host "  API       : $ApiImg"
Write-Host "  Frontend  : $FeImg"
Write-Host ""

# ---------------------------------------------------------------------------
# Registry push path -- single manifest list covers all platforms
# ---------------------------------------------------------------------------
if ($Push) {
  $nc = if ($NoC) { " --no-cache" } else { "" }

  Write-Host "[1/2] Building + pushing API image ($Platforms)..." -ForegroundColor Yellow
  Run "docker buildx build --platform $Platforms --tag $ApiImg --tag ${Prefix}/api:latest$nc --push ./backend" `
      "API build/push failed"

  Write-Host "[2/2] Building + pushing frontend image ($Platforms)..." -ForegroundColor Yellow
  Run "docker buildx build --platform $Platforms --tag $FeImg --tag ${Prefix}/frontend:latest$nc --push --build-arg VITE_API_URL= -f frontend/Dockerfile ." `
      "Frontend build/push failed"

  Write-Host ""
  Write-Host "[OK] Multi-arch images pushed." -ForegroundColor Green
  Write-Host ""
  Write-Host "     On any host (x86, ARM, Apple Silicon) just run:"
  Write-Host "       .\make.ps1 up-prebuilt"
  Write-Host ""
  Write-Host "     Add to .env on restricted hosts:"
  Write-Host "       IMAGE_REGISTRY=$Registry"
  Write-Host "       IMAGE_REPO=$Repo"
  Write-Host "       IMAGE_TAG=$Tag"

# ---------------------------------------------------------------------------
# Local tar export path (no registry configured)
# ---------------------------------------------------------------------------
} else {
  New-Item -ItemType Directory -Force -Path "dist" | Out-Null
  $ArchList = $Platforms -split ","
  $nc = if ($NoC) { " --no-cache" } else { "" }

  foreach ($Platform in $ArchList) {
    $Arch       = $Platform -replace "linux/", ""
    $ApiArchImg = "${Repo}/api:${Tag}-${Arch}"
    $FeArchImg  = "${Repo}/frontend:${Tag}-${Arch}"

    Write-Host "[>] Building + loading API [$Platform]..." -ForegroundColor Yellow
    Run "docker buildx build --platform $Platform --tag $ApiArchImg$nc --load ./backend" `
        "API build failed for $Platform"
    docker save $ApiArchImg | gzip > "dist\jenkins-analyzer-api-${Tag}-${Arch}.tar.gz"

    Write-Host "[>] Building + loading frontend [$Platform]..." -ForegroundColor Yellow
    Run "docker buildx build --platform $Platform --tag $FeArchImg$nc --load --build-arg VITE_API_URL= -f frontend/Dockerfile ." `
        "Frontend build failed for $Platform"
    docker save $FeArchImg | gzip > "dist\jenkins-analyzer-frontend-${Tag}-${Arch}.tar.gz"
  }

  Write-Host ""
  Write-Host "[OK] Archives saved to .\dist\" -ForegroundColor Green
  Get-ChildItem "dist\jenkins-analyzer-*-${Tag}-*.tar.gz" -ErrorAction SilentlyContinue |
    ForEach-Object { Write-Host "     $($_.Name)  ($([math]::Round($_.Length/1MB, 1)) MB)" }
  Write-Host ""
  Write-Host "Load the archive matching the target host architecture:"
  Write-Host "  x86 / AMD64:"
  Write-Host "    docker load < dist\jenkins-analyzer-api-${Tag}-amd64.tar.gz"
  Write-Host "    docker load < dist\jenkins-analyzer-frontend-${Tag}-amd64.tar.gz"
  Write-Host "  ARM64 / Apple Silicon / Graviton:"
  Write-Host "    docker load < dist\jenkins-analyzer-api-${Tag}-arm64.tar.gz"
  Write-Host "    docker load < dist\jenkins-analyzer-frontend-${Tag}-arm64.tar.gz"
  Write-Host ""
  Write-Host "After loading, set IMAGE_TAG=${Tag}-<arch> in .env, then: .\make.ps1 up-prebuilt"
}
