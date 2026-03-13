@echo off
:: Jenkins Performance Analyzer - Windows Setup Launcher
:: Double-click this file to get started, or run from Command Prompt.

title Jenkins Performance Analyzer

echo.
echo   Jenkins Performance Analyzer
echo   =========================================================
echo.
echo   Checking prerequisites...
echo.

docker info >nul 2>&1
if errorlevel 1 (
    echo   ERROR: Docker is not running.
    echo   Start Docker Desktop and try again.
    echo.
    pause
    exit /b 1
)
echo   [OK] Docker is running

docker compose version >nul 2>&1
if errorlevel 1 (
    echo   ERROR: docker compose not found. Update Docker Desktop to 4.x+.
    pause
    exit /b 1
)
echo   [OK] Docker Compose found

echo.
echo   =========================================================
echo   Choose a run mode:
echo.
echo   [1] Cloud AI only         - Anthropic / private endpoint (no local model)
echo       Any machine. Requires ANTHROPIC_API_KEY in .env.
echo.
echo   [2] Dockerized Ollama CPU  - Ollama in Docker on CPU (Mac, Linux, Windows)
echo       First run downloads the model automatically.
echo.
echo   [3] Host Ollama            - Use Ollama installed on this machine (not Docker)
echo       Mac recommended. Faster start, no container overhead.
echo       Requires: ollama serve  (running with OLLAMA_HOST=0.0.0.0 on Mac/Linux)
echo.
echo   [4] Ollama GPU             - Ollama with NVIDIA GPU (Windows / Linux)
echo       Requires NVIDIA GPU + Docker Desktop WSL2 GPU passthrough.
echo.
echo   [5] Stop             - Stop all containers
echo   [6] Logs             - View container logs
echo   [7] Status           - Show running containers
echo   [8] Check GPU        - Verify NVIDIA prerequisites
echo   [9] Open app         - Open browser to frontend
echo   [Q] Quit
echo.
set /p CHOICE=  Enter choice: 

if /i "%CHOICE%"=="1" goto CLOUD
if /i "%CHOICE%"=="2" goto CPU
if /i "%CHOICE%"=="3" goto HOSTOLLAMA
if /i "%CHOICE%"=="4" goto GPU
if /i "%CHOICE%"=="5" goto STOP
if /i "%CHOICE%"=="6" goto LOGS
if /i "%CHOICE%"=="7" goto STATUS
if /i "%CHOICE%"=="8" goto CHECKGPU
if /i "%CHOICE%"=="9" goto OPEN
if /i "%CHOICE%"=="Q" goto END
if /i "%CHOICE%"=="q" goto END
goto END

:CLOUD
echo.
echo   Starting cloud AI mode...
powershell -ExecutionPolicy Bypass -File make.ps1 up
pause
goto END

:CPU
echo.
echo   Starting local Ollama (CPU mode)...
echo   NOTE: First run will download the model. This may take several minutes.
echo   TIP:  For faster CPU inference set OLLAMA_MODEL=phi3:mini in .env
echo.
powershell -ExecutionPolicy Bypass -File make.ps1 up-ollama
pause
goto END

:HOSTOLLAMA
echo.
echo   Starting with host-native Ollama...
echo   Make sure Ollama is running: ollama serve
echo   (On Mac/Linux set OLLAMA_HOST=0.0.0.0 so Docker can reach it)
echo.
docker compose -f docker-compose.yml -f docker-compose.host-ollama.yml up --build -d
pause
goto END

:GPU
echo.
echo   Starting local Ollama (GPU mode, requires NVIDIA)...
powershell -ExecutionPolicy Bypass -File make.ps1 up-gpu
pause
goto END

:STOP
echo.
echo   Stopping all containers...
docker compose down
docker compose --profile ollama down 2>nul
docker compose -f docker-compose.yml -f docker-compose.host-ollama.yml down 2>nul
pause
goto END

:LOGS
echo.
echo   Press Ctrl+C to stop log streaming.
docker compose logs -f
goto END

:STATUS
echo.
docker compose ps
pause
goto END

:CHECKGPU
echo.
powershell -ExecutionPolicy Bypass -File scripts\check-gpu.ps1
pause
goto END

:OPEN
echo.
set FRONTEND_PORT=3000
if exist .env (
    for /f "tokens=2 delims==" %%a in ('findstr /i "FRONTEND_PORT" .env') do set FRONTEND_PORT=%%a
)
echo   Opening http://localhost:%FRONTEND_PORT%
start http://localhost:%FRONTEND_PORT%
goto END

:END
echo.
