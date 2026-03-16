"""
src/control/prompts/extract_invoice.py
Updated to support multi-file attachment context.
"""
from pathlib import Path
from typing import List, Dict, Optional
from poml import poml as render_poml

PROMPT_NAME    = "extract_invoice_data"
PROMPT_VERSION = "1.1"
_TEMPLATE = str(Path(__file__).parent / "templates" / "extract_invoice.poml")


def build_extract_invoice_prompt(
    raw_text: str,
    attachment_metadata: Optional[List[Dict]] = None,
) -> str:
    """
    Build the invoice extraction prompt.
    attachment_metadata: list of {file_name, file_type, extracted_text}
    If provided, attachment contents are appended so the LLM can cross-reference
    CSV line items, Excel PO data, PDF invoices, etc.
    """
    att_block = ""
    if attachment_metadata:
        parts = []
        for meta in attachment_metadata:
            fname     = meta.get("file_name", "attachment")
            ftype     = meta.get("file_type", "unknown")
            extracted = meta.get("extracted_text", "")
            if extracted:
                parts.append(f"[{fname} ({ftype.upper()})]\n{extracted[:2000]}")
        att_block = "\n\n---\n\n".join(parts)[:6000]

    # Combine body text with attachment content for LLM
    combined = raw_text[:4000]
    if att_block:
        combined = f"{combined}\n\n=== ATTACHMENT CONTENT ===\n{att_block}"

    context = {"raw_text": combined[:8000]}
    messages = render_poml(_TEMPLATE, context)
    return "\n\n".join(m["content"] for m in messages if m.get("content"))
