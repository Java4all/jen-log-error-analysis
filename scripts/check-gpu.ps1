# Jenkins Performance Analyzer - GPU Prerequisite Check (Windows)
# Usage: .\scripts\check-gpu.ps1
#        .\make.ps1 check-gpu

function Write-Pass { param($msg) Write-Host "  [PASS] $msg" -ForegroundColor Green }
function Write-Fail { param($msg) Write-Host "  [FAIL] $msg" -ForegroundColor Red }
function Write-Warn { param($msg) Write-Host "  [WARN] $msg" -ForegroundColor Yellow }
function Write-Info { param($msg) Write-Host "         $msg" -ForegroundColor DarkGray }
function Write-Head { param($msg) Write-Host ""; Write-Host "  --- $msg ---" -ForegroundColor Cyan }

Write-Host ""
Write-Host "  Jenkins Analyzer - GPU Prerequisite Check (Windows)" -ForegroundColor Cyan
Write-Host "  ======================================================" -ForegroundColor Cyan

$totalErrors = 0

# --- 1. NVIDIA driver + nvidia-smi -------------------------------------------
Write-Head "NVIDIA GPU"
$smiCmd = Get-Command nvidia-smi -ErrorAction SilentlyContinue
if ($null -ne $smiCmd) {
    $gpuName  = nvidia-smi --query-gpu=name           --format=csv,noheader 2>$null | Select-Object -First 1
    $driver   = nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>$null | Select-Object -First 1
    $vramMbRaw = nvidia-smi --query-gpu=memory.total  --format=csv,noheader,nounits 2>$null | Select-Object -First 1
    $vramMb   = [int]$vramMbRaw
    $vramGb   = [math]::Round($vramMb / 1024, 1)
    Write-Pass "nvidia-smi found"
    Write-Info "GPU    : $gpuName"
    Write-Info "Driver : $driver"
    Write-Info "VRAM   : $vramGb GB"

    $driverMajor = [int]($driver.Trim().Split(".")[0])
    if ($driverMajor -ge 525) {
        Write-Pass "NVIDIA driver >= 525 (CUDA 12 compatible)"
    }
    else {
        Write-Fail "NVIDIA driver $driver is too old. Need >= 525 for CUDA 12."
        Write-Info "Update at: https://www.nvidia.com/Download/index.aspx"
        $totalErrors++
    }
}
else {
    Write-Fail "nvidia-smi not found - NVIDIA drivers not installed or not in PATH"
    Write-Info "Download at: https://www.nvidia.com/Download/index.aspx"
    $totalErrors++
}

# --- 2. Windows build ---------------------------------------------------------
Write-Head "Windows Version"
$build = [System.Environment]::OSVersion.Version.Build
if ($build -ge 19044) {
    Write-Pass "Windows build $build (WSL2 GPU passthrough supported)"
}
elseif ($build -ge 18362) {
    Write-Warn "Windows build $build - WSL2 available but GPU passthrough needs 21H2+"
    Write-Info "Update Windows to 21H2 (build 19044) or later via Windows Update."
}
else {
    Write-Fail "Windows build $build - WSL2 not supported. Need Windows 10 1903+ (build 18362)."
    $totalErrors++
}

# --- 3. WSL2 ------------------------------------------------------------------
Write-Head "WSL2"
$wslCmd = Get-Command wsl -ErrorAction SilentlyContinue
if ($null -ne $wslCmd) {
    $wslOut = wsl --status 2>&1 | Out-String
    if ($wslOut -match "2") {
        Write-Pass "WSL2 is installed and set as default"
    }
    else {
        $wslList = wsl -l -v 2>&1 | Out-String
        if ($wslList -match "2") {
            Write-Pass "WSL2 distro found"
        }
        else {
            Write-Warn "WSL found but no WSL2 distro detected"
            Write-Info "Run: wsl --set-default-version 2"
            Write-Info "Then: wsl --install -d Ubuntu"
        }
    }
}
else {
    Write-Fail "WSL not found"
    Write-Info "Run in elevated PowerShell: wsl --install"
    $totalErrors++
}

# --- 4. Docker Desktop --------------------------------------------------------
Write-Head "Docker Desktop"
$dockerCmd = Get-Command docker -ErrorAction SilentlyContinue
if ($null -ne $dockerCmd) {
    $dockerVerRaw = docker --version 2>&1
    $dockerVer = ($dockerVerRaw -replace "Docker version ", "" -replace ",.*", "").Trim()
    Write-Pass "Docker CLI found (v$dockerVer)"

    $null = docker info 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Pass "Docker daemon is running"

        $dockerInfoOut = docker info 2>&1 | Out-String
        if ($dockerInfoOut -match "wsl") {
            Write-Pass "Docker Desktop using WSL2 backend"
        }
        else {
            Write-Warn "Docker Desktop may not be using WSL2 backend"
            Write-Info "Settings -> General -> tick 'Use WSL 2 based engine', then Apply & Restart"
        }
    }
    else {
        Write-Fail "Docker daemon is not running - start Docker Desktop first"
        $totalErrors++
    }
}
else {
    Write-Fail "Docker Desktop not found"
    Write-Info "Download at: https://www.docker.com/products/docker-desktop/"
    $totalErrors++
}

# --- 5. Docker Compose v2 -----------------------------------------------------
Write-Head "Docker Compose v2"
$null = docker compose version 2>&1
if ($LASTEXITCODE -eq 0) {
    $composeVerRaw = docker compose version 2>&1
    $composeVer = ($composeVerRaw -replace "Docker Compose version v", "").Trim()
    Write-Pass "docker compose plugin found (v$composeVer)"
}
else {
    Write-Fail "docker compose plugin not found - update Docker Desktop to 4.x+"
    $totalErrors++
}

# --- 6. GPU in Docker (WSL2 passthrough) --------------------------------------
Write-Head "GPU in Docker (WSL2 passthrough)"
$null = docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi -L 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Pass "GPU is accessible inside Docker containers"
}
else {
    Write-Warn "Could not verify GPU inside Docker"
    Write-Info "This is expected if Docker Desktop was not restarted after driver install."
    Write-Info "Test manually: docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi"
    Write-Info ""
    Write-Info "Checklist:"
    Write-Info "  1. NVIDIA driver >= 525 installed on Windows (not inside WSL)"
    Write-Info "  2. Docker Desktop -> Resources -> WSL Integration -> enable your distro"
    Write-Info "  3. Restart Docker Desktop after any driver or settings change"
}

# --- 7. Model recommendation --------------------------------------------------
Write-Head "Model Recommendation"
if ($null -ne $smiCmd) {
    $vramMbFinal = [int](nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>$null | Select-Object -First 1)
    $vramGbFinal = [math]::Round($vramMbFinal / 1024, 1)
    Write-Host "  Detected VRAM: ~$vramGbFinal GB" -ForegroundColor White
    Write-Host ""
    if ($vramMbFinal -ge 24000) {
        Write-Host "  Recommended: codellama:13b or llama3:13b" -ForegroundColor Green
        Write-Host "  Set in .env: OLLAMA_MODEL=codellama:13b" -ForegroundColor DarkGray
    }
    elseif ($vramMbFinal -ge 8000) {
        Write-Host "  Recommended: codellama:7b or mistral:7b  (fits in 8 GB)" -ForegroundColor Yellow
        Write-Host "  Set in .env: OLLAMA_MODEL=codellama:7b" -ForegroundColor DarkGray
    }
    else {
        Write-Host "  Recommended: phi3:mini or codellama:7b-q4  (low VRAM)" -ForegroundColor Red
        Write-Host "  Set in .env: OLLAMA_MODEL=phi3:mini" -ForegroundColor DarkGray
    }
}
else {
    Write-Host "  No GPU detected - cannot make recommendation." -ForegroundColor DarkGray
}

# --- Summary ------------------------------------------------------------------
Write-Host ""
Write-Host "  ======================================================" -ForegroundColor DarkGray
if ($totalErrors -eq 0) {
    Write-Host "  All prerequisites met. Run:  .\make.ps1 up-gpu" -ForegroundColor Green
}
else {
    Write-Host "  $totalErrors prerequisite(s) failed. Fix the issues above before running GPU mode." -ForegroundColor Red
}
Write-Host ""
