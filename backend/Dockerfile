# -----------------------------------------------------------------------------
# Jenkins Performance Analyzer -- Backend
# Single image based on python:3.12-slim.
# GPU access is handled by Docker Desktop passing the NVIDIA device to the
# Ollama container -- this API container communicates with Ollama over HTTP
# and requires no CUDA libraries itself.
# -----------------------------------------------------------------------------

FROM python:3.12-slim

LABEL org.opencontainers.image.title="Jenkins Analyzer API"
LABEL org.opencontainers.image.description="Jenkins performance analysis backend"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential git curl gosu \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd -m -u 1001 appuser && chown -R appuser:appuser /app
RUN chmod +x /app/entrypoint.sh

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
