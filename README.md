# Jenkins Performance Analyzer

AI-powered Jenkins build log analysis with source code correlation.  
Runs entirely in Docker -- Linux, macOS, and **Windows** all supported.

---

## Architecture

```
Browser
  \-- :3000  nginx (frontend container)
               +-- /          -> React SPA
               +-- /api/*     -> proxy -> api:8000  (FastAPI Python)
               \-- /health    -> proxy -> api:8000

api:8000  FastAPI backend
  +-- ai_service.py      -> Anthropic | Ollama (GPU) | Private endpoint
  +-- github_service.py  -> Fetch + parse source repos
  \-- log_parser.py      -> Parse Jenkins logs, build call trees

ollama:11434  (GPU profile only)
  \-- NVIDIA CUDA container -> local LLM (codellama, mistral, llama3...)
```

---

## Windows Setup

### Prerequisites

| Requirement | Version | Download |
|---|---|---|
| Windows | 10 21H2+ or Windows 11 | Windows Update |
| Docker Desktop | 4.x+ | https://www.docker.com/products/docker-desktop/ |
| NVIDIA driver (GPU only) | >= 525 | https://www.nvidia.com/Download/index.aspx |

### Docker Desktop settings (required)

Open Docker Desktop -> **Settings**:
1. **General** -> [OK] _Use the WSL 2 based engine_
2. **Resources -> WSL Integration** -> [OK] Enable your WSL distro
3. Click **Apply & Restart**

### Running on Windows

**Option A -- Double-click launcher** (easiest):
```
setup.bat
```
A menu guides you through start / stop / GPU check / open browser.

**Option B -- PowerShell** (full control):
```powershell
# First time setup
.\make.ps1 setup          # creates .env from .env.example
notepad .env              # add your ANTHROPIC_API_KEY

# CPU mode (cloud AI)
.\make.ps1 up

# GPU mode (local Ollama)
.\make.ps1 check-gpu      # verify prerequisites
.\make.ps1 up-gpu

# Open app
start http://localhost:3000
```

**Option B -- Linux/macOS (Makefile)**:
```bash
make setup
make up          # CPU
make up-gpu      # GPU
```

### PowerShell execution policy

If PowerShell blocks `.ps1` scripts, run once in an elevated PowerShell:
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

---

## All Commands

### PowerShell (Windows)

```powershell
.\make.ps1 help           # show all commands

.\make.ps1 setup          # create .env
.\make.ps1 up             # CPU: build + start
.\make.ps1 up-gpu         # GPU: build + start + pull model
.\make.ps1 down           # stop
.\make.ps1 down-gpu       # stop GPU stack

.\make.ps1 logs           # tail all logs
.\make.ps1 logs-api       # API logs only
.\make.ps1 ps             # container status
.\make.ps1 health         # check /health endpoints

.\make.ps1 check-gpu      # verify NVIDIA prerequisites
.\make.ps1 pull-model     # pull/update Ollama model
.\make.ps1 shell-api      # bash into API container

.\make.ps1 clean          # remove local images
.\make.ps1 nuke           # remove everything including volumes
```

### Linux / macOS (Makefile)

```bash
make up          make up-gpu
make down        make down-gpu
make logs        make health
make check-gpu   make shell-api
make clean       make nuke
```

---

## NVIDIA GPU on Windows

Docker Desktop on Windows exposes NVIDIA GPUs to Linux containers through
**WSL2** -- no extra Linux driver installation needed inside WSL2.

```
Windows Host
  NVIDIA Driver (Windows) <- only driver you install
       v GPU passthrough
  WSL2 kernel
       v
  Docker Desktop (WSL2 backend)
       v --gpus all
  Linux container (nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04)
       v
  Ollama / llama.cpp / vLLM
```

### VRAM guide

| GPU VRAM | Recommended model | `.env` setting |
|---|---|---|
| >= 24 GB | codellama:13b / llama3:13b | `OLLAMA_MODEL=codellama:13b` |
| >= 8 GB  | codellama:7b / mistral:7b  | `OLLAMA_MODEL=codellama:7b` |
| < 8 GB  | phi3:mini / codellama:7b-q4 | `OLLAMA_MODEL=phi3:mini` |

### Verify GPU works in Docker

```powershell
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

---

## Configuration

Edit `config/config.yaml` (hot-reloaded -- no container restart needed):

```yaml
ai:
  provider: "ollama"      # anthropic | ollama | private
  gpu_enabled: true
  gpu_layers: 35

pipeline:
  static_tags:
    - "service-abc"
    - "service-deploy"

github:
  type: "private"
  token: "env:GITHUB_TOKEN"
  repos:
    - url: "https://github.com/your-org/jenkins-libs"
      branch: "main"
      enabled: true
```

---

## Project Structure

```
jenkins-analyzer/
+-- backend/
|   +-- Dockerfile         # Multi-stage: builder -> cpu | gpu (CUDA 12.4)
|   +-- main.py            # FastAPI -- 8 REST endpoints
|   +-- ai_service.py      # Anthropic / Ollama / private abstraction
|   +-- github_service.py  # Source fetching + method correlation
|   +-- log_parser.py      # Log parsing + prompt builder
|   \-- config.py          # Pydantic config loader
+-- frontend/
|   +-- Dockerfile         # Node builder -> nginx:alpine
|   +-- src/App.jsx        # React UI
|   \-- vite.config.js     # Dev proxy + build config
+-- nginx/
|   \-- nginx.conf         # Reverse proxy + SSE support + SPA routing
+-- config/
|   \-- config.yaml        # Main configuration (hot-reloaded)
+-- scripts/
|   +-- check-gpu.sh       # GPU checker (Linux/macOS)
|   \-- check-gpu.ps1      # GPU checker (Windows)
+-- docker-compose.yml     # Base CPU stack
+-- docker-compose.gpu.yml # GPU overlay
+-- Makefile               # Linux/macOS commands
+-- make.ps1               # Windows PowerShell commands
+-- setup.bat              # Windows double-click launcher
+-- .gitattributes         # LF/CRLF line-ending rules
\-- .env.example           # Environment variable template
```
