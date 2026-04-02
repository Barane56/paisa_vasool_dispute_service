"""
src/control/prompts/extract_contract_rates.py
"""

from __future__ import annotations

from pathlib import Path

from poml import poml as render_poml

PROMPT_NAME = "extract_contract_rates"
PROMPT_VERSION = "1.0"
_TEMPLATE = str(Path(__file__).parent / "templates" / "extract_contract_rates.poml")


def build_extract_contract_rates_prompt(
    contract_text: str,
    invoice_number: str,
    invoice_amount: str | None = None,
    line_items: str | None = None,
) -> str:
    context = {
        "contract_text": contract_text[:8000],  # Limit text length
        "invoice_number": invoice_number,
        "invoice_amount": invoice_amount or "Not provided",
        "line_items": line_items or "Not provided",
    }

    messages = render_poml(_TEMPLATE, context)
    return "\n\n".join(m["content"] for m in messages if m.get("content"))
