"""
src/control/prompts/structure_email.py
======================================
Prompt builder for the structural triage step.
No dispute types are passed here — this prompt only decides
how many issues an email contains and describes each one.
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import List, Optional, Dict

from poml import poml as render_poml

PROMPT_NAME    = "structure_email"
PROMPT_VERSION = "1.1"
_TEMPLATE = str(Path(__file__).parent / "templates" / "structure_email.poml")


def build_structure_prompt(
    subject: str,
    sender_email: str,
    body_text: str,
    attachment_texts: List[str],
    groq_extracted: Optional[dict] = None,
    attachment_metadata: Optional[List[Dict]] = None,  # NEW: [{file_name, file_type, extracted_text}]
) -> str:
    # Build enriched attachment block including file-type context
    att_block = _build_attachment_block(attachment_texts, attachment_metadata)
    context = {
        "subject":         subject,
        "sender_email":    sender_email,
        "body_text":       body_text[:2000],
        "attachment_text": att_block,
        "groq_extracted":  json.dumps(groq_extracted) if groq_extracted else "",
    }
    messages = render_poml(_TEMPLATE, context)
    return "\n\n".join(m["content"] for m in messages if m.get("content"))


def _build_attachment_block(
    attachment_texts: List[str],
    attachment_metadata: Optional[List[Dict]] = None,
) -> str:
    """
    Build a rich attachment context block for the LLM.
    Includes file-type labels so the model understands what each chunk came from.
    """
    if not attachment_texts and not attachment_metadata:
        return ""

    parts = []
    if attachment_metadata:
        for i, meta in enumerate(attachment_metadata):
            fname     = meta.get("file_name", f"attachment_{i+1}")
            ftype     = meta.get("file_type", "unknown")
            extracted = meta.get("extracted_text") or (attachment_texts[i] if i < len(attachment_texts) else "")
            if extracted:
                parts.append(
                    f"[{fname} ({ftype.upper()})]\n{extracted[:1500]}"
                )
    elif attachment_texts:
        parts = [t[:800] for t in attachment_texts if t]

    return "\n\n---\n\n".join(parts)[:4000]
