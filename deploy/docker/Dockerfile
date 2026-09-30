# syntax=docker/dockerfile:1
# One image definition, three deployable targets: gateway, console-api, console-ui.
#   docker build -f deploy/docker/Dockerfile --target gateway -t agentops-gateway .

FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"
RUN pip install --no-cache-dir uv==0.11.4
WORKDIR /app

# Dependencies first so code changes do not invalidate this layer.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project --extra pgvector --extra ui

COPY . .
RUN uv sync --frozen --no-dev --extra pgvector --extra ui

# Optionally bake the real embedding model into the image (needs network at build time).
ARG PREFETCH_EMBEDDINGS=false
ENV FASTEMBED_CACHE_PATH=/opt/fastembed
RUN if [ "$PREFETCH_EMBEDDINGS" = "true" ]; then \
      python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5')"; \
    fi

# Run as an unprivileged user; data/ is writable for the SQLite fallback and seeded inventory.
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /opt/fastembed \
    && chown -R app:app /app/data /opt/fastembed
USER app

# ------------------------------------------------------------------ gateway
FROM base AS gateway
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=30s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz')"
CMD ["uvicorn", "gateway.main:app", "--host", "0.0.0.0", "--port", "8000"]

# ------------------------------------------------------------------ console-api
FROM base AS console-api
EXPOSE 8001
HEALTHCHECK --interval=15s --timeout=3s --start-period=30s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8001/healthz')"
CMD ["uvicorn", "console.api.main:app", "--host", "0.0.0.0", "--port", "8001"]

# ------------------------------------------------------------------ console-ui
FROM base AS console-ui
EXPOSE 8501
HEALTHCHECK --interval=15s --timeout=3s --start-period=30s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')"
CMD ["streamlit", "run", "console/ui/app.py", "--server.port=8501", "--server.address=0.0.0.0", \
     "--server.headless=true", "--browser.gatherUsageStats=false"]
