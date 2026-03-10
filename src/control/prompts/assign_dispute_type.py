"""
src/control/prompts/assign_dispute_type.py
==========================================
Prompt builder for the type-assignment step.
Called once per issue (primary + each additional) AFTER the structural
triage has already decided how many issues exist.
"""
from __future__ import annotations
from pathlib import Path
from typing import List, Dict, Optional

from poml import poml as render_poml

PROMPT_NAME    = "assign_dispute_type"
PROMPT_VERSION = "1.0"
_TEMPLATE = str(Path(__file__).parent / "templates" / "assign_dispute_type.poml")


def build_assign_type_prompt(
    classification: str,
    description: str,
    available_dispute_types: List[Dict],
    invoice_number: Optional[str] = None,
) -> str:
    types_block = "\n".join([
        f"- {dt['reason_name']}: {dt.get('description', '')} (severity: {dt.get('severity_level', 'MEDIUM')})"
        for dt in available_dispute_types
    ]) or "None defined yet"

    context = {
        "classification":  classification,
        "description":     description,
        "invoice_number":  invoice_number or "",
        "dispute_types":   types_block,
    }
    messages = render_poml(_TEMPLATE, context)
    return "\n\n".join(m["content"] for m in messages if m.get("content"))
