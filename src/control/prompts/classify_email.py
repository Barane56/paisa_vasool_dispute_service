"""
src/control/prompts/classify_email.py
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import List, Dict, Optional
from poml import poml as render_poml

PROMPT_NAME    = "classify_email"
PROMPT_VERSION = "1.0"
_TEMPLATE = str(Path(__file__).parent / "templates" / "classify_email.poml")


def build_classify_prompt(
    subject: str,
    sender_email: str,
    body_text: str,
    attachment_texts: List[str],
    available_dispute_types: List[Dict],
    groq_extracted: Optional[Dict] = None,
) -> str:
    types_block = "\n".join([
        f"- {dt['reason_name']}: {dt.get('description', '')} (severity: {dt.get('severity_level', 'MEDIUM')})"
        for dt in available_dispute_types
    ]) or "None defined yet"

    context = {
        "subject":         subject,
        "sender_email":    sender_email,
        "body_text":       body_text[:1000],
        "attachment_text": " ".join(attachment_texts)[:500],
        "groq_extracted":  json.dumps(groq_extracted) if groq_extracted else "",
        "dispute_types":   types_block,
    }

    messages = render_poml(_TEMPLATE, context)
    return "\n\n".join(m["content"] for m in messages if m.get("content"))
