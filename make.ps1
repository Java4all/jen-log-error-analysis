# Jenkins Performance Analyzer - Windows PowerShell build tool
# Equivalent of the Linux Makefile.
#
# Usage:
#   .\make.ps1               show help
#   .\make.ps1 setup         create .env from .env.example
#   .\make.ps1 up            CPU mode: build + start
#   .\make.ps1 up-gpu        GPU mode: build + start + pull model
#   .\make.ps1 down          stop stack
#   .\make.ps1 logs          tail all logs
#   .\make.ps1 health        check service health

param(
    [Parameter(Position=0)]
    [string]$Command = "help"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# --- Colour helpers -----------------------------------------------------------
function Write-Green  { param($msg) Write-Host $msg -ForegroundColor Green }
function Write-Yellow { param($msg) Write-Host $msg -ForegroundColor Yellow }
function Write-Red    { param($msg) Write-Host $msg -ForegroundColor Red }
function Write-Cyan   { param($msg) Write-Host $msg -ForegroundColor Cyan }
function Write-Gray   { param($msg) Write-Host $msg -ForegroundColor DarkGray }

# --- Load .env into current process environment ------------------------------
function Load-Env {
    if (Test-Path ".env") {
        Get-Content ".env" | ForEach-Object {
            $line = $_.Trim()
            if ($line -and -not $line.StartsWith("#") -and $line -match "^([^=]+)=(.*)$") {
                $envKey   = $Matches[1].Trim()
                $envValue = $Matches[2].Trim().Trim('"').Trim("'")
                [System.Environment]::SetEnvironmentVariable($envKey, $envValue, "Process")
            }
        }
    }
}

function Get-EnvVal {
    param($envKey, $envDefault)
    $val = [System.Environment]::GetEnvironmentVariable($envKey)
    if ($val) { return $val }
    return $envDefault
}

# --- Prerequisite guards -----------------------------------------------------
function Assert-Docker {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Write-Red "ERROR: Docker not found."
        Write-Red "       Install Docker Desktop: https://www.docker.com/products/docker-desktop/"
        exit 1
    }
    $null = docker info 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Red "ERROR: Docker daemon is not running. Start Docker Desktop and try again."
        exit 1
    }
}

function Assert-Compose {
    $null = docker compose version 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Red "ERROR: docker compose plugin not found. Update Docker Desktop to 4.x+."
        exit 1
    }
}

# --- Compose wrappers --------------------------------------------------------
# NOTE: $Args is a reserved PowerShell automatic variable -- never use it as a param name.
# Use explicit named params instead.

function Run-Compose {
    param([string]$ComposeArgs)
    $cmdLine = "docker compose $ComposeArgs"
    Write-Gray "  > $cmdLine"
    Invoke-Expression $cmdLine
}

function Run-ComposeOllama {
    param([string]$ComposeArgs)
    $cmdLine = "docker compose --profile ollama $ComposeArgs"
    Write-Gray "  > $cmdLine"
    Invoke-Expression $cmdLine
}

function Run-ComposeGpu {
    param([string]$ComposeArgs)
    # Use an array to avoid PowerShell string-parsing issues with multiple -f flags
    $cmdLine = "docker compose -f docker-compose.yml -f docker-compose.gpu.yml --profile gpu $ComposeArgs"
    Write-Gray "  > $cmdLine"
    Invoke-Expression $cmdLine
}

# =============================================================================
# COMMANDS
# =============================================================================

function Run-ComposeIsolatedOllama {
    param([string]$ComposeArgs)
    $cmdLine = "docker compose -f docker-compose.yml -f docker-compose.isolated.yml --profile ollama $ComposeArgs"
    Write-Gray "  > $cmdLine"
    Invoke-Expression $cmdLine
}

function Run-ComposeIsolatedGpu {
    param([string]$ComposeArgs)
    $cmdLine = "docker compose -f docker-compose.yml -f docker-compose.gpu.yml -f docker-compose.isolated.yml --profile gpu $ComposeArgs"
    Write-Gray "  > $cmdLine"
    Invoke-Expression $cmdLine
}

function Cmd-Help {
    Write-Host ""
    Write-Cyan "  Jenkins Performance Analyzer - Windows PowerShell tool"
    Write-Cyan "  ========================================================="
    Write-Host ""
    Write-Host "  SETUP"
    Write-Gray "  .\make.ps1 setup           create .env from .env.example"
    Write-Host ""
    Write-Host "  CLOUD AI MODE  (Anthropic / private endpoint, no local model)"
    Write-Gray "  .\make.ps1 up              build + start API + frontend"
    Write-Gray "  .\make.ps1 down            stop"
    Write-Gray "  .\make.ps1 build           rebuild images only"
    Write-Host ""
    Write-Host "  ISOLATED MODE  (no internet -- for air-gapped / enterprise envs)"
    Write-Gray "  .\make.ps1 up-ollama-isolated  Ollama CPU, internet blocked"
    Write-Gray "  .\make.ps1 up-gpu-isolated     Ollama GPU, internet blocked"
    Write-Gray "  .\make.ps1 down-isolated       stop isolated stack"
    Write-Host ""
    Write-Host "  Pre-built images (restricted/air-gapped):"
    Write-Host "  Multi-arch / pre-built images:"
    Write-Gray "  .\make.ps1 buildx-setup            create multi-arch builder (once per machine)"
    Write-Gray "  .\make.ps1 push-images             build amd64+arm64 images and push"
    Write-Gray "  .\make.ps1 push-images-amd64       build amd64 only"
    Write-Gray "  .\make.ps1 push-images-arm64       build arm64 only"
    Write-Gray "  .\make.ps1 up-prebuilt             run pre-built images (cloud AI)"
    Write-Gray "  .\make.ps1 up-prebuilt-ollama      run pre-built + Ollama CPU"
    Write-Gray "  .\make.ps1 up-prebuilt-gpu         run pre-built + Ollama GPU"
    Write-Gray "  .\make.ps1 up-prebuilt-isolated    run pre-built + private-only mode"
    Write-Host ""
    Write-Host "  LOCAL OLLAMA, CPU MODE  (any machine, including Mac)"
    Write-Gray "  .\make.ps1 up-ollama       build + start + Ollama on CPU"
    Write-Gray "  .\make.ps1 down-ollama     stop"
    Write-Host ""
    Write-Host "  LOCAL OLLAMA, GPU MODE  (Windows / Linux with NVIDIA GPU)"
    Write-Gray "  .\make.ps1 up-gpu          build + start + Ollama on GPU"
    Write-Gray "  .\make.ps1 down-gpu        stop GPU stack"
    Write-Gray "  .\make.ps1 build-gpu       rebuild GPU API image only"
    Write-Gray "  .\make.ps1 pull-model      manually pull/update Ollama model"
    Write-Host ""
    Write-Host "  UTILITIES"
    Write-Gray "  .\make.ps1 logs            tail all container logs"
    Write-Gray "  .\make.ps1 logs-api        tail API logs only"
    Write-Gray "  .\make.ps1 logs-frontend   tail frontend logs"
    Write-Gray "  .\make.ps1 ps              show container status"
    Write-Gray "  .\make.ps1 health          check service health endpoints"
    Write-Gray "  .\make.ps1 shell-api       open shell in API container"
    Write-Gray "  .\make.ps1 restart         restart all services"
    Write-Gray "  .\make.ps1 check-gpu       verify NVIDIA GPU prerequisites"
    Write-Gray "  .\make.ps1 clean           remove local images"
    Write-Gray "  .\make.ps1 nuke            remove containers + images + volumes"
    Write-Host ""
}

function Cmd-Setup {
    if (-not (Test-Path ".env")) {
        Copy-Item ".env.example" ".env"
        Write-Green "  .env created - edit it and add your API keys before starting."
        Write-Yellow "  Open with:  notepad .env"
    }
    else {
        Write-Yellow "  .env already exists - skipping. Delete it to reset."
    }
}

function Cmd-Up {
    Assert-Docker
    Assert-Compose
    Cmd-Setup
    Load-Env
    Write-Green "Starting CPU stack..."
    Run-Compose "up --build -d"
    if ($LASTEXITCODE -eq 0) {
        $fp = Get-EnvVal "FRONTEND_PORT" "3000"
        $ap = Get-EnvVal "API_PORT" "8000"
        Write-Host ""
        Write-Green "Stack is running:"
        Write-Cyan  "  Frontend  -> http://localhost:$fp"
        Write-Cyan  "  API docs  -> http://localhost:$ap/docs"
        Write-Host ""
    }
}

function Cmd-Down {
    Assert-Docker
    Run-Compose "down"
}

function Cmd-Build {
    Assert-Docker
    Assert-Compose
    Run-Compose "build"
}

function Cmd-UpOllama {
    Assert-Docker
    Assert-Compose
    Cmd-Setup
    Load-Env
    Write-Green "Starting Ollama CPU stack (works on Mac, Linux, Windows)..."
    Write-Yellow "First run will download the model -- this may take several minutes."
    $model = Get-EnvVal "OLLAMA_MODEL" "codellama:13b"
    Write-Host "  Model: $model"
    Write-Host "  Tip: for faster CPU inference use a smaller model."
    Write-Host "       Set OLLAMA_MODEL=phi3:mini or OLLAMA_MODEL=codellama:7b in .env"
    Write-Host ""
    # Set AI_PROVIDER=ollama in .env
    if (Test-Path ".env") {
        $envContent = Get-Content ".env" -Raw
        if ($envContent -match "(?m)^AI_PROVIDER=") {
            $envContent = $envContent -replace "(?m)^AI_PROVIDER=.*", "AI_PROVIDER=ollama"
        } else {
            $envContent += "`r`nAI_PROVIDER=ollama"
        }
        Set-Content ".env" $envContent
    }
    Run-ComposeOllama "up --build -d"
    if ($LASTEXITCODE -eq 0) {
        $fp = Get-EnvVal "FRONTEND_PORT" "3000"
        $ap = Get-EnvVal "API_PORT" "8000"
        $op = Get-EnvVal "OLLAMA_PORT" "11434"
        Write-Host ""
        Write-Green "Ollama CPU stack is running:"
        Write-Cyan  "  Frontend  -> http://localhost:$fp"
        Write-Cyan  "  API docs  -> http://localhost:$ap/docs"
        Write-Cyan  "  Ollama    -> http://localhost:$op"
        Write-Host ""
    }
}

function Cmd-DownOllama {
    Assert-Docker
    Run-ComposeOllama "down"
}

function Cmd-UpGpu {
    Assert-Docker
    Assert-Compose
    Cmd-Setup
    Load-Env

    Write-Host ""
    Write-Yellow "Checking NVIDIA GPU..."
    $smiFound = Get-Command nvidia-smi -ErrorAction SilentlyContinue
    if ($null -ne $smiFound) {
        $gpuLine = nvidia-smi --query-gpu=name --format=csv,noheader 2>$null | Select-Object -First 1
        Write-Green "  GPU found: $gpuLine"
    }
    else {
        Write-Yellow "  nvidia-smi not found - Docker Desktop WSL2 may still have GPU access."
    }
    Write-Host ""

    Write-Green "Starting GPU stack (NVIDIA CUDA + Ollama)..."
    Run-ComposeGpu "up --build -d"
    if ($LASTEXITCODE -eq 0) {
        $fp    = Get-EnvVal "FRONTEND_PORT" "3000"
        $ap    = Get-EnvVal "API_PORT" "8000"
        $op    = Get-EnvVal "OLLAMA_PORT" "11434"
        $model = Get-EnvVal "OLLAMA_MODEL" "codellama:13b"
        Write-Host ""
        Write-Green "GPU stack is running:"
        Write-Cyan  "  Frontend  -> http://localhost:$fp"
        Write-Cyan  "  API docs  -> http://localhost:$ap/docs"
        Write-Cyan  "  Ollama    -> http://localhost:$op"
        Write-Host  "  Model: $model (being pulled by ollama-init)"
        Write-Host ""
    }
}

function Cmd-DownGpu {
    Assert-Docker
    Run-ComposeGpu "down"
}

function Cmd-BuildGpu {
    Assert-Docker
    Assert-Compose
    Run-ComposeGpu "build api"
}

function Cmd-Logs {
    Assert-Docker
    Run-Compose "logs -f"
}

function Cmd-LogsApi {
    Assert-Docker
    Run-Compose "logs -f api"
}

function Cmd-LogsFrontend {
    Assert-Docker
    Run-Compose "logs -f frontend"
}

function Cmd-Ps {
    Assert-Docker
    Run-Compose "ps"
}

function Cmd-Restart {
    Assert-Docker
    Run-Compose "restart"
}

function Cmd-Health {
    Load-Env
    $ap = Get-EnvVal "API_PORT" "8000"
    $fp = Get-EnvVal "FRONTEND_PORT" "3000"

    Write-Host ""
    Write-Cyan "--- API health ---"
    try {
        $resp = Invoke-RestMethod "http://localhost:$ap/health" -TimeoutSec 5
        Write-Green "  API is UP"
        Write-Host  "  Provider : $($resp.ai_provider)"
        Write-Host  "  GPU      : $($resp.gpu_enabled)"
        Write-Host  "  Tags     : $($resp.pipeline_tags -join ', ')"
    }
    catch {
        Write-Red "  API not responding on port $ap"
    }

    Write-Host ""
    Write-Cyan "--- Frontend ---"
    try {
        $null = Invoke-WebRequest "http://localhost:$fp" -TimeoutSec 5 -UseBasicParsing
        Write-Green "  Frontend is UP at http://localhost:$fp"
    }
    catch {
        Write-Red "  Frontend not responding on port $fp"
    }
    Write-Host ""
}

function Cmd-PullModel {
    Load-Env
    $op    = Get-EnvVal "OLLAMA_PORT" "11434"
    $model = Get-EnvVal "OLLAMA_MODEL" "codellama:13b"
    Write-Yellow "Pulling model: $model"
    $body = ConvertTo-Json @{ name = $model }
    Invoke-RestMethod -Uri "http://localhost:$op/api/pull" -Method POST `
        -Body $body -ContentType "application/json"
}

function Cmd-ShellApi {
    Assert-Docker
    docker compose exec api /bin/bash
    if ($LASTEXITCODE -ne 0) {
        docker compose exec api /bin/sh
    }
}

function Cmd-CheckGpu {
    $scriptPath = Join-Path $PSScriptRoot "scripts\check-gpu.ps1"
    if (Test-Path $scriptPath) {
        & $scriptPath
    }
    else {
        Write-Red "check-gpu.ps1 not found at $scriptPath"
    }
}

function Cmd-Clean {
    Assert-Docker
    Run-Compose "down --rmi local"
    docker image rm jenkins-analyzer-api:gpu 2>$null
    Write-Green "Local images removed."
}

function Cmd-Nuke {
    Write-Red "WARNING: This removes ALL containers, images, and volumes"
    Write-Red "         (including the Ollama model cache)."
    $confirm = Read-Host "Type 'yes' to confirm"
    if ($confirm -eq "yes") {
        Run-Compose "down -v --rmi all"
        try { Run-ComposeGpu "down -v --rmi all" } catch { }
        Write-Green "All resources removed."
    }
    else {
        Write-Yellow "Cancelled."
    }
}

function Cmd-UpOllamaIsolated {
    Assert-Docker
    Assert-Compose
    Cmd-Setup
    Load-Env
    Write-Green "Starting isolated Ollama CPU stack (no public internet)..."
    Run-ComposeIsolatedOllama "up --build -d"
    if ($LASTEXITCODE -eq 0) {
        $fp = Get-EnvVal "FRONTEND_PORT" "3000"
        $ap = Get-EnvVal "API_PORT" "8000"
        Write-Host ""
        Write-Green "Isolated stack is running:"
        Write-Yellow "  [ISOLATED] No outbound internet access"
        Write-Cyan   "  Frontend  -> http://localhost:$fp"
        Write-Cyan   "  API docs  -> http://localhost:$ap/docs"
        Write-Host ""
    }
}

function Cmd-UpGpuIsolated {
    Assert-Docker
    Assert-Compose
    Cmd-Setup
    Load-Env
    Write-Green "Starting isolated Ollama GPU stack (no public internet)..."
    Run-ComposeIsolatedGpu "up --build -d"
    if ($LASTEXITCODE -eq 0) {
        $fp = Get-EnvVal "FRONTEND_PORT" "3000"
        $ap = Get-EnvVal "API_PORT" "8000"
        Write-Host ""
        Write-Green "Isolated GPU stack is running:"
        Write-Yellow "  [ISOLATED] No outbound internet access"
        Write-Cyan   "  Frontend  -> http://localhost:$fp"
        Write-Cyan   "  API docs  -> http://localhost:$ap/docs"
        Write-Host ""
    }
}

function Cmd-DownIsolated {
    Assert-Docker
    try { Run-ComposeIsolatedOllama "down" } catch { }
    try { Run-ComposeIsolatedGpu "down" } catch { }
}

# =============================================================================
# PRE-BUILT IMAGE COMMANDS
# =============================================================================
function Cmd-BuildxSetup {
    Write-Host "[>]  Setting up multi-arch buildx builder..." -ForegroundColor Cyan
    & "$PSScriptRoot\scripts\push-images.ps1" -Setup
}

function Cmd-PushImages {
    Write-Host "[>]  Building multi-arch (amd64+arm64) images and pushing..." -ForegroundColor Cyan
    & "$PSScriptRoot\scripts\push-images.ps1"
}

function Cmd-PushImagesAmd64 {
    Write-Host "[>]  Building amd64-only images and pushing..." -ForegroundColor Cyan
    & "$PSScriptRoot\scripts\push-images.ps1" -Amd64Only
}

function Cmd-PushImagesArm64 {
    Write-Host "[>]  Building arm64-only images and pushing..." -ForegroundColor Cyan
    & "$PSScriptRoot\scripts\push-images.ps1" -Arm64Only
}

function Cmd-UpPrebuilt {
    Assert-Docker
    Write-Host "[>]  Starting pre-built stack (cloud AI)..." -ForegroundColor Cyan
    docker compose -f docker-compose.yml -f docker-compose.prebuilt.yml up -d
    Write-Host "[OK] Stack running on http://localhost:$(if ($env:FRONTEND_PORT) { $env:FRONTEND_PORT } else { '3000' })" -ForegroundColor Green
}

function Cmd-UpPrebuiltOllama {
    Assert-Docker
    Write-Host "[>]  Starting pre-built stack (Ollama CPU)..." -ForegroundColor Cyan
    docker compose -f docker-compose.yml -f docker-compose.prebuilt.yml --profile ollama up -d
    Write-Host "[OK] Stack running on http://localhost:$(if ($env:FRONTEND_PORT) { $env:FRONTEND_PORT } else { '3000' })" -ForegroundColor Green
}

function Cmd-UpPrebuiltGpu {
    Assert-Docker
    Write-Host "[>]  Starting pre-built stack (Ollama GPU)..." -ForegroundColor Cyan
    docker compose -f docker-compose.yml -f docker-compose.gpu.yml -f docker-compose.prebuilt.yml --profile gpu up -d
    Write-Host "[OK] Stack running on http://localhost:$(if ($env:FRONTEND_PORT) { $env:FRONTEND_PORT } else { '3000' })" -ForegroundColor Green
}

function Cmd-UpPrebuiltIsolated {
    Assert-Docker
    Write-Host "[>]  Starting pre-built private-only stack (Ollama CPU)..." -ForegroundColor Cyan
    docker compose -f docker-compose.yml -f docker-compose.prebuilt.yml -f docker-compose.isolated.yml --profile ollama up -d
    Write-Host "[OK] Pre-built private-only stack running" -ForegroundColor Green
}

function Cmd-UpPrebuiltGpuIsolated {
    Assert-Docker
    Write-Host "[>]  Starting pre-built private-only stack (Ollama GPU)..." -ForegroundColor Cyan
    docker compose -f docker-compose.yml -f docker-compose.gpu.yml -f docker-compose.prebuilt.yml -f docker-compose.isolated.yml --profile gpu up -d
    Write-Host "[OK] Pre-built private-only GPU stack running" -ForegroundColor Green
}

# =============================================================================
# ROUTER
# =============================================================================
switch ($Command.ToLower()) {
    "help"          { Cmd-Help }
    "setup"         { Cmd-Setup }
    "up"            { Cmd-Up }
    "down"          { Cmd-Down }
    "build"         { Cmd-Build }
    "up-ollama-isolated" { Cmd-UpOllamaIsolated }
    "up-gpu-isolated"    { Cmd-UpGpuIsolated }
    "down-isolated"      { Cmd-DownIsolated }
    "up-ollama"     { Cmd-UpOllama }
    "down-ollama"   { Cmd-DownOllama }
    "up-gpu"        { Cmd-UpGpu }
    "down-gpu"      { Cmd-DownGpu }
    "build-gpu"     { Cmd-BuildGpu }
    "logs"          { Cmd-Logs }
    "logs-api"      { Cmd-LogsApi }
    "logs-frontend" { Cmd-LogsFrontend }
    "ps"            { Cmd-Ps }
    "restart"       { Cmd-Restart }
    "health"        { Cmd-Health }
    "pull-model"    { Cmd-PullModel }
    "shell-api"     { Cmd-ShellApi }
    "check-gpu"     { Cmd-CheckGpu }
    "clean"         { Cmd-Clean }
    "nuke"          { Cmd-Nuke }
    "buildx-setup"             { Cmd-BuildxSetup }
    "push-images"              { Cmd-PushImages }
    "push-images-amd64"        { Cmd-PushImagesAmd64 }
    "push-images-arm64"        { Cmd-PushImagesArm64 }
    "up-prebuilt"              { Cmd-UpPrebuilt }
    "up-prebuilt-ollama"       { Cmd-UpPrebuiltOllama }
    "up-prebuilt-gpu"          { Cmd-UpPrebuiltGpu }
    "up-prebuilt-isolated"     { Cmd-UpPrebuiltIsolated }
    "up-prebuilt-gpu-isolated" { Cmd-UpPrebuiltGpuIsolated }
    default {
        Write-Red "Unknown command: $Command"
        Write-Host ""
        Cmd-Help
        exit 1
    }
}
