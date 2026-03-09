"""
src/control/prompts/extract_invoice.py
"""
from pathlib import Path
from poml import poml as render_poml

PROMPT_NAME    = "extract_invoice_data"
PROMPT_VERSION = "1.0"
_TEMPLATE = str(Path(__file__).parent / "templates" / "extract_invoice.poml")


def build_extract_invoice_prompt(raw_text: str) -> str:
    context = {"raw_text": raw_text[:6000]}
    messages = render_poml(_TEMPLATE, context)
    return "\n\n".join(m["content"] for m in messages if m.get("content"))
