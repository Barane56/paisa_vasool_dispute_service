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
import re
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
        self.model           = settings.GROQ_MODEL            # 70b — heavy tasks only
        self.fast_model      = settings.GROQ_FAST_MODEL       # 8b — extract, summarize
        self.reasoning_model = settings.GROQ_REASONING_MODEL  # qwen/qwen3-32b — classify, detect context shift
        self.invoice_model   = settings.GROQ_INVOICE_MODEL

    # ------------------------------------------------------------------ #
    # Generic chat                                                        #
    # ------------------------------------------------------------------ #
    async def chat(self, prompt: str, system: str = None, json_mode: bool = True) -> str:
        """
        Generic chat using the 70b model.

        Handles Groq's json_validate_failed (400) gracefully: when the model
        produces structurally invalid JSON (e.g. ai_response as an object instead
        of a string), we send a correction turn and retry once before raising.
        """
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

        max_attempts = 2
        for attempt in range(1, max_attempts + 1):
            try:
                response = await self.client.chat.completions.create(**kwargs)
                return response.choices[0].message.content
            except Exception as e:
                err_str = str(e)
                # Groq returns 400 json_validate_failed when the model puts
                # ai_response as a JSON object instead of a plain string.
                # Extract the failed_generation and send a correction turn.
                if "json_validate_failed" in err_str and attempt < max_attempts:
                    logger.warning(
                        f"Groq json_validate_failed on attempt {attempt} — "
                        "sending correction turn and retrying."
                    )
                    # Extract what the model tried to send so it can self-correct
                    import re as _re
                    bad_match = _re.search(r"'failed_generation': '(.*?)'(?:,|\})", err_str, _re.DOTALL)
                    bad_output = bad_match.group(1) if bad_match else "(see previous attempt)"
                    messages = [
                        *messages,
                        {"role": "assistant", "content": bad_output},
                        {"role": "user", "content": (
                            "Your previous response was rejected because ai_response "
                            "was a JSON object instead of a plain string. "
                            "ai_response MUST be a single JSON string with \\n for line breaks — "
                            "NOT a nested object or array. "
                            "Return the corrected JSON now with ai_response as a plain string."
                        )},
                    ]
                    kwargs["messages"] = messages
                    continue
                logger.error(f"Groq chat error: {e}")
                raise LLMError(f"Groq API request failed: {e}")

    # ------------------------------------------------------------------ #
    # Fast chat — uses 8b model for simple classify/extract/detect tasks  #
    # ------------------------------------------------------------------ #
    async def chat_fast(self, prompt: str, system: str = None, json_mode: bool = True) -> str:
        """Same as chat() but uses GROQ_FAST_MODEL (8b) to save quota."""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        kwargs = dict(
            model=self.fast_model,
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
            logger.error(f"Groq fast chat error: {e}")
            raise LLMError(f"Groq API request failed: {e}")

    # ------------------------------------------------------------------ #
    # Reasoning chat — qwen/qwen3-32b for classify + context shift      #
    #                                                                    #
    # Groq supports reasoning_format="hidden" which suppresses the      #
    # <think> block entirely — clean JSON lands directly in content.    #
    # Also supports json_object response_format unlike qwen-qwq-32b.   #
    #                                                                    #
    # Guardrails:                                                        #
    #   1. reasoning_format=hidden — no thinking pollution in output    #
    #   2. response_format=json_object — Groq enforces valid JSON       #
    #   3. Post-parse validation — catch any edge-case malformed output #
    #   4. Retry up to MAX_RETRIES with correction turn on bad output   #
    # ------------------------------------------------------------------ #

    _REASONING_MAX_RETRIES = 3

    async def chat_reasoning(self, prompt: str, system: str = None, json_mode: bool = True) -> str:
        """
        Uses GROQ_REASONING_MODEL (qwen/qwen3-32b) with strict guardrails.

        reasoning_format="hidden" tells Groq to suppress the <think> block
        and return only the final answer — no manual stripping needed.

        response_format=json_object is supported by qwen3-32b on Groq,
        so JSON compliance is enforced at the API level too.

        Retries up to _REASONING_MAX_RETRIES times with a correction turn
        if the output somehow fails json.loads() despite the above.
        """
        messages = []
        if system:
            messages.append({"role": "system", "content": system})

        json_suffix = (
            "\n\nReturn ONLY a single valid JSON object. "
            "No markdown. No code fences. No commentary. "
            "Start with { and end with }."
        ) if json_mode else ""
        messages.append({"role": "user", "content": prompt + json_suffix})

        kwargs = dict(
            model=self.reasoning_model,
            messages=messages,
            temperature=0.1,
            max_tokens=4096,
            reasoning_format="hidden",   # suppress <think> block entirely
        )
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        last_error: Exception | None = None

        for attempt in range(1, self._REASONING_MAX_RETRIES + 1):
            try:
                response = await self.client.chat.completions.create(**kwargs)
                raw = response.choices[0].message.content or ""

                # Clean any residual artifacts (defensive — hidden mode should be clean)
                cleaned = self._clean_reasoning_output(raw)

                if json_mode:
                    try:
                        json.loads(cleaned)
                    except json.JSONDecodeError as parse_err:
                        logger.warning(
                            f"chat_reasoning attempt {attempt}/{self._REASONING_MAX_RETRIES}: "
                            f"non-JSON output despite json_object format — retrying. "
                            f"err={parse_err} snippet={cleaned[:200]!r}"
                        )
                        last_error = parse_err
                        messages = [
                            *messages,
                            {"role": "assistant", "content": raw},
                            {"role": "user", "content": (
                                "Your previous response was not valid JSON. "
                                "Output ONLY the JSON object — no markdown, no explanation. "
                                "Start immediately with { and end with }."
                            )},
                        ]
                        continue

                return cleaned

            except LLMError:
                raise
            except Exception as exc:
                logger.error(
                    f"chat_reasoning attempt {attempt}/{self._REASONING_MAX_RETRIES} "
                    f"API error: {exc}"
                )
                last_error = exc
                if attempt < self._REASONING_MAX_RETRIES:
                    import asyncio as _asyncio
                    await _asyncio.sleep(1.5 * attempt)

        raise LLMError(
            f"chat_reasoning failed after {self._REASONING_MAX_RETRIES} attempts. "
            f"Last error: {last_error}"
        )

    @staticmethod
    def _clean_reasoning_output(raw: str) -> str:
        """
        Defensive cleanup for qwen3-32b output.
        With reasoning_format=hidden this should be a no-op in practice,
        but we keep it as a safety net for any residual artifacts.
        """
        # Remove any stray <think> blocks (shouldn't appear with hidden mode)
        cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()

        # Strip markdown fences
        if "```" in cleaned:
            cleaned = re.sub(r"```[a-z]*\n?", "", cleaned)
            cleaned = re.sub(r"\n?```", "", cleaned)
            cleaned = cleaned.strip()

        # If preamble text still wraps the JSON, extract first { } block
        if cleaned and not cleaned.startswith(("{", "[")):
            obj_match = re.search(r"(\{.*\}|\[.*\])", cleaned, flags=re.DOTALL)
            if obj_match:
                cleaned = obj_match.group(0).strip()

        return cleaned

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
                model=self.fast_model,   # summarization doesn't need 70b
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