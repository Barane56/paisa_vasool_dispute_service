"""
src/control/agents/nodes/extract_invoice.py
"""

from __future__ import annotations
import logging
from typing import Optional, List, Dict

from src.observability import observe, langfuse_context
from src.control.agents.state import EmailProcessingState
from src.control.agents.nodes.extract_text import _regex_invoice_numbers

logger = logging.getLogger(__name__)


@observe(name="node_extract_invoice_data")
async def node_extract_invoice_data_via_groq(
    state: EmailProcessingState, llm_client=None
) -> EmailProcessingState:
    groq_extracted: Optional[Dict] = None
    candidates: List[str] = []

    if llm_client:
        try:
            groq_extracted = await llm_client.extract_invoice_data(
                state["all_text"],
                attachment_metadata=state.get("attachment_metadata"),
            )
            langfuse_context.update_current_observation(
                input={"text_length": len(state["all_text"])},
                output={"invoice_number": groq_extracted.get("invoice_number")},
            )
            inv_num = groq_extracted.get("invoice_number")
            if inv_num:
                candidates.append(str(inv_num).upper().strip())
            po = groq_extracted.get("po_number")
            if po:
                candidates.append(str(po).upper().strip())
        except Exception as e:
            logger.warning(
                f"[email_id={state['email_id']}] Groq invoice extraction failed: {e}. "
                f"Falling back to regex."
            )

    for c in _regex_invoice_numbers(state["all_text"]):
        if c not in candidates:
            candidates.append(c)

    logger.info(f"[email_id={state['email_id']}] Invoice candidates: {candidates}")
    return {**state, "groq_extracted": groq_extracted, "candidate_invoice_numbers": candidates}
