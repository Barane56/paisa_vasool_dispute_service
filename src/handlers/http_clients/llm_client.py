"""
Groq LLM client – drop-in replacement for the previous OpenAI client.

Uses the Groq REST API (OpenAI-compatible endpoint) via the `groq` SDK.
Models used:
  - Chat / classification / response generation  → GROQ_MODEL      (default: llama-3.3-70b-versatile)
  - Invoice data extraction                       → GROQ_INVOICE_MODEL (same model, separate method for clarity)

NOTE: Groq does not provide an embedding API.  Embeddings are skipped
      (content_embedding stored as NULL).  Swap in a separate provider
      (e.g. Cohere, local sentence-transformers) if you need vector search.
"""

import json
import logging
from groq import AsyncGroq
from src.config.settings import settings
from src.core.exceptions import LLMError, InvoiceExtractionError

logger = logging.getLogger(__name__)


class LLMClient:
    def __init__(self):
        self.client = AsyncGroq(api_key=settings.GROQ_API_KEY)
        self.model = settings.GROQ_MODEL
        self.invoice_model = settings.GROQ_INVOICE_MODEL

    # ------------------------------------------------------------------ #
    # Generic chat – always returns a raw string                          #
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
    # Invoice data extraction  (dedicated method for clarity)             #
    # ------------------------------------------------------------------ #
    async def extract_invoice_data(self, raw_text: str) -> dict:
        """
        Send raw PDF/attachment text to Groq and intelligently extract:
          - invoice_number
          - invoice_date
          - due_date
          - vendor_name
          - customer_name
          - customer_id  (if present)
          - line_items   (list of {description, qty, unit_price, total})
          - subtotal
          - tax_amount
          - total_amount
          - currency
          - payment_terms
          - po_number    (if present)
          - notes        (any other relevant info)

        Returns a dict with the above keys (missing fields → null/None).
        Raises InvoiceExtractionError on failure.
        """
        prompt = f"""You are a financial document parser.
Extract all relevant invoice information from the text below and return ONLY valid JSON.

TEXT:
\"\"\"
{raw_text[:6000]}
\"\"\"

Return ONLY valid JSON with these exact keys (use null for missing fields):
{{
  "invoice_number": "string or null",
  "invoice_date": "YYYY-MM-DD or null",
  "due_date": "YYYY-MM-DD or null",
  "vendor_name": "string or null",
  "customer_name": "string or null",
  "customer_id": "string or null",
  "line_items": [
    {{
      "description": "string",
      "qty": number_or_null,
      "unit_price": number_or_null,
      "total": number_or_null
    }}
  ],
  "subtotal": number_or_null,
  "tax_amount": number_or_null,
  "total_amount": number_or_null,
  "currency": "USD or string or null",
  "payment_terms": "string or null",
  "po_number": "string or null",
  "notes": "string or null"
}}"""

        try:
            response = await self.client.chat.completions.create(
                model=self.invoice_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=2048,
                response_format={"type": "json_object"},
            )
            raw = response.choices[0].message.content
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
    # Summarization                                                        #
    # ------------------------------------------------------------------ #
    async def summarize_episodes(self, episodes: list, existing_summary: str = None) -> str:
        context = ""
        if existing_summary:
            context = f"EXISTING SUMMARY:\n{existing_summary}\n\nNEW EPISODES TO INCORPORATE:\n"

        episodes_text = "\n".join([
            f"[{ep.get('actor', 'UNKNOWN')}] {ep.get('content_text', '')[:300]}"
            for ep in episodes
        ])

        prompt = f"""{context}
Episodes:
{episodes_text}

Summarize the full dispute conversation history in 3-5 sentences.
Focus on: what the customer complained about, what was already asked/answered, current status.
Return a plain text summary (NOT JSON).
"""
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
    # Stub embed (Groq has no embedding API)                              #
    # ------------------------------------------------------------------ #
    async def embed(self, text: str) -> None:  # type: ignore[override]
        """Returns None – embeddings not available via Groq."""
        logger.debug("embed() called but Groq has no embedding API – returning None")
        return None


_llm_client: LLMClient | None = None


def get_llm_client() -> LLMClient:
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient()
    return _llm_client
