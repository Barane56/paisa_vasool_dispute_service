"""
Groq LLM client with local fastembed embeddings.

Chat / classification / response generation  → Groq (llama-3.3-70b-versatile)
Invoice data extraction                       → Groq (same model)
Embeddings                                    → Local fastembed (BAAI/bge-small-en-v1.5)

Why fastembed over sentence-transformers?
  - Uses ONNX runtime instead of PyTorch → ~200MB total vs ~2GB
  - No torch dependency
  - Same model, same 384 dims, same quality
  - Faster on CPU

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SWAPPING EMBEDDING MODEL IN THE FUTURE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Only two things need to change:

1. settings.py:
     EMBEDDING_MODEL = "BAAI/bge-base-en-v1.5"   # was bge-small-en-v1.5
     EMBEDDING_DIMS  = 768                         # was 384

2. One SQL migration:
     ALTER TABLE dispute_memory_episode
     ALTER COLUMN content_embedding TYPE vector(768);

Nothing else in this file needs to change.

Install:
  uv add fastembed
"""

import logging
import json
import threading
from typing import Optional, List

from groq import AsyncGroq
from fastembed import TextEmbedding

from src.config.settings import settings
from src.core.exceptions import LLMError, InvoiceExtractionError
from src.observability import observe, langfuse_context
from src.control.prompts import build_extract_invoice_prompt, build_summarize_episodes_prompt
from src.control.prompts.extract_invoice import PROMPT_NAME as EXTRACT_PROMPT_NAME, PROMPT_VERSION as EXTRACT_PROMPT_VERSION
from src.control.prompts.summarize_episodes import PROMPT_NAME as SUMMARIZE_PROMPT_NAME, PROMPT_VERSION as SUMMARIZE_PROMPT_VERSION

logger = logging.getLogger(__name__)


# ─── Embedding model singleton ────────────────────────────────────────────────
# Loaded once at first use (lazy) — model downloads on first call (~33MB for bge-small).
# All model/dims config lives in settings — swap there, nothing here changes.

_embed_model: Optional[TextEmbedding] = None
_embed_lock = threading.Lock()


def _get_embed_model() -> TextEmbedding:
    global _embed_model
    if _embed_model is None:
        with _embed_lock:
            if _embed_model is None:
                import os
                # Honour FASTEMBED_CACHE_PATH if set (matches the Docker build-time path).
                # When the env var is set the model is already on disk — no download happens.
                cache_dir = os.environ.get("FASTEMBED_CACHE_PATH") or None
                logger.info(
                    f"Loading local embedding model: {settings.EMBEDDING_MODEL} "
                    f"(dims={settings.EMBEDDING_DIMS}, cache={cache_dir or 'default'})"
                )
                _embed_model = TextEmbedding(
                    model_name=settings.EMBEDDING_MODEL,
                    cache_dir=cache_dir,
                )
    return _embed_model


# ─── LLM Client ───────────────────────────────────────────────────────────────

class LLMClient:
    def __init__(self):
        self.client        = AsyncGroq(api_key=settings.GROQ_API_KEY)
        self.model         = settings.GROQ_MODEL
        self.invoice_model = settings.GROQ_INVOICE_MODEL

    # ------------------------------------------------------------------ #
    # Generic chat                                                        #
    # ------------------------------------------------------------------ #
    async def chat(self, prompt: str, system: str = None, json_mode: bool = True) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        kwargs = dict(
            model=self.model,
            messages=messages,
            temperature=0.1,
            max_tokens=1024,
        )
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        try:
            response = await self.client.chat.completions.create(**kwargs)
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"Groq chat error: {e}")
            raise LLMError(f"Groq API request failed: {e}")

    # ------------------------------------------------------------------ #
    # Invoice data extraction                                             #
    # ------------------------------------------------------------------ #
    @observe(name="llm_extract_invoice_data")
    async def extract_invoice_data(self, raw_text: str, attachment_metadata: list = None) -> dict:
        prompt = build_extract_invoice_prompt(raw_text, attachment_metadata=attachment_metadata)
        langfuse_context.update_current_observation(
            input={"prompt": prompt},
            metadata={"prompt_name": EXTRACT_PROMPT_NAME, "prompt_version": EXTRACT_PROMPT_VERSION},
        )

        try:
            response = await self.client.chat.completions.create(
                model=self.invoice_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=2048,
                response_format={"type": "json_object"},
            )
            raw  = response.choices[0].message.content
            data = json.loads(raw)
            logger.info(f"Invoice extraction succeeded. invoice_number={data.get('invoice_number')}")
            return data
        except json.JSONDecodeError as e:
            logger.error(f"Invoice extraction JSON parse error: {e}")
            raise InvoiceExtractionError(f"Could not parse LLM response as JSON: {e}")
        except Exception as e:
            logger.error(f"Invoice extraction LLM error: {e}")
            raise InvoiceExtractionError(str(e))

    # ------------------------------------------------------------------ #
    # Summarization                                                       #
    # ------------------------------------------------------------------ #
    @observe(name="llm_summarize_episodes")
    async def summarize_episodes(self, episodes: list, existing_summary: str = None) -> str:
        prompt = build_summarize_episodes_prompt(episodes, existing_summary)
        langfuse_context.update_current_observation(
            input={"prompt": prompt},
            metadata={"prompt_name": SUMMARIZE_PROMPT_NAME, "prompt_version": SUMMARIZE_PROMPT_VERSION},
        )
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=512,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"Groq summarization error: {e}")
            raise LLMError(f"Summarization failed: {e}")

    # ------------------------------------------------------------------ #
    # Local embeddings via fastembed                                      #
    # ------------------------------------------------------------------ #
    async def embed(self, text: str) -> Optional[List[float]]:
        """
        Generate a local embedding using fastembed (ONNX runtime, no torch).

        fastembed.embed() is synchronous and returns a generator — we grab
        the first (and only) result with next().

        To upgrade to a larger model later:
          settings.py  → EMBEDDING_MODEL / EMBEDDING_DIMS
          SQL migration → ALTER COLUMN content_embedding TYPE vector(<new_dims>)
        """
        if not text or not text.strip():
            logger.warning("embed() called with empty text — returning None")
            return None

        try:
            model  = _get_embed_model()
            # embed() returns a generator of numpy arrays, one per input string
            vector = next(model.embed([text]))
            return vector.tolist()
        except Exception as e:
            logger.error(f"Local embedding error: {e}")
            return None


# ─── Singleton ────────────────────────────────────────────────────────────────

_llm_client: Optional[LLMClient] = None
_client_lock = threading.Lock()


def get_llm_client() -> LLMClient:
    global _llm_client
    if _llm_client is None:
        with _client_lock:
            if _llm_client is None:
                _llm_client = LLMClient()
    return _llm_client