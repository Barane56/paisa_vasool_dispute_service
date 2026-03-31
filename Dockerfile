FROM python:3.13-slim-bookworm

# ── Environment & Optimization ───────────────────────────────────────────────
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    FASTEMBED_CACHE_PATH=/app/.fastembed_cache

WORKDIR /app

# ── System deps ───────────────────────────────────────────────────────────────
# libgomp1 is required by the ONNX runtime that fastembed uses internally
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# ── Dependency management (uv) ───────────────────────────────────────────────
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Install dependencies before copying source for better caching
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# ── Pre-download BAAI embedding model at build time ───────────────────────────
RUN uv run python - <<'PYEOF'
from fastembed import TextEmbedding
import os

model_name = os.environ.get("EMBEDDING_MODEL", "BAAI/bge-base-en-v1.5")
print(f"Pre-downloading embedding model: {model_name}")
model = TextEmbedding(model_name=model_name)
list(model.embed(["warmup"]))
print(f"Model ready at: {os.environ['FASTEMBED_CACHE_PATH']}")
PYEOF

# ── App source & Permissions ──────────────────────────────────────────────────
COPY . .

# Create non-root user and set permissions
RUN groupadd -r appgroup && useradd -r -g appgroup -u 1000 appuser \
    && chown -R appuser:appgroup /app

USER appuser

# ── Runtime ───────────────────────────────────────────────────────────────────
ENV PATH="/app/.venv/bin:$PATH"
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8002"]
