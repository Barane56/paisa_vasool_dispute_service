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
from typing import List, Optional

from poml import poml as render_poml

PROMPT_NAME    = "structure_email"
PROMPT_VERSION = "1.0"
_TEMPLATE = str(Path(__file__).parent / "templates" / "structure_email.poml")


def build_structure_prompt(
    subject: str,
    sender_email: str,
    body_text: str,
    attachment_texts: List[str],
    groq_extracted: Optional[dict] = None,
) -> str:
    context = {
        "subject":         subject,
        "sender_email":    sender_email,
        "body_text":       body_text[:2000],
        "attachment_text": " ".join(attachment_texts)[:800],
        "groq_extracted":  json.dumps(groq_extracted) if groq_extracted else "",
    }
    messages = render_poml(_TEMPLATE, context)
    return "\n\n".join(m["content"] for m in messages if m.get("content"))
