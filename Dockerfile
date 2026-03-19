FROM python:3.11-slim-bookworm

WORKDIR /app

# ── System deps ───────────────────────────────────────────────────────────────
# libgomp1 is required by the ONNX runtime that fastembed uses internally
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# ── Python deps ───────────────────────────────────────────────────────────────
COPY requirements/requirements.txt requirements/requirements.txt
RUN pip install --no-cache-dir -r requirements/requirements.txt

# ── Pre-download BAAI embedding model at build time ───────────────────────────
# fastembed downloads ONNX model files into FASTEMBED_CACHE_PATH.
# Doing this during docker build bakes the model (~140 MB for bge-base-en-v1.5)
# into the image layer — zero download time at container startup.
#
# The cache path is set explicitly so it's predictable and consistent between
# build and runtime. The same env var is passed at runtime (see CMD / compose).
#
# To swap models later: change the model name here AND in settings.py.
ENV FASTEMBED_CACHE_PATH=/app/.fastembed_cache

RUN python - <<'PYEOF'
from fastembed import TextEmbedding
import os

model_name = os.environ.get("EMBEDDING_MODEL", "BAAI/bge-base-en-v1.5")
print(f"Pre-downloading embedding model: {model_name}")
# Instantiating TextEmbedding triggers the download + ONNX conversion
model = TextEmbedding(model_name=model_name)
# Run one dummy embed to force full initialisation and verify the model works
list(model.embed(["warmup"]))
print(f"Model ready at: {os.environ['FASTEMBED_CACHE_PATH']}")
PYEOF

# ── App source ────────────────────────────────────────────────────────────────
COPY . .

# ── Runtime ───────────────────────────────────────────────────────────────────
# FASTEMBED_CACHE_PATH must match the build-time path so the pre-downloaded
# model is found instead of re-downloaded.
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8002"]
