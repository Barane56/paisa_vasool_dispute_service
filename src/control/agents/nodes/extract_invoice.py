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

# Maps groq_extracted field names → ar_document_key key_type values
_REF_FIELD_TO_KEY_TYPE: Dict[str, str] = {
    "po_number":       "po_number",
    "grn_number":      "grn_number",
    "payment_ref":     "payment_ref",
    "contract_number": "contract_number",
}


@observe(name="node_extract_invoice_data")
async def node_extract_invoice_data_via_groq(
    state: EmailProcessingState, llm_client=None
) -> EmailProcessingState:
    groq_extracted: Optional[Dict] = None
    candidates:     List[str]      = []
    # Non-invoice AR references for graph walk fallback
    # Each entry: {"value": "<raw_ref>", "key_type": "<ar_key_type>"}
    candidate_references: List[Dict] = []

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

            # ── Invoice number → candidate_invoice_numbers ────────────────────
            inv_num = (groq_extracted.get("invoice_number") or "").strip()
            if inv_num:
                candidates.append(inv_num.upper())

            # ── PO number: both a candidate invoice number AND a reference ────
            # It goes into candidate_invoice_numbers so identify_invoice can
            # match it against the invoice DB; it also goes into
            # candidate_references so fetch_context can walk the AR graph.
            po = (groq_extracted.get("po_number") or "").strip()
            if po:
                po_upper = po.upper()
                if po_upper not in candidates:
                    candidates.append(po_upper)
                candidate_references.append({"value": po_upper, "key_type": "po_number"})

            # ── GRN / payment_ref / contract: references only (not invoice IDs)
            for field, key_type in _REF_FIELD_TO_KEY_TYPE.items():
                if field == "po_number":
                    continue  # already handled above
                raw = (groq_extracted.get(field) or "").strip()
                if raw:
                    candidate_references.append({"value": raw.upper(), "key_type": key_type})

        except Exception as e:
            logger.warning(
                f"[email_id={state['email_id']}] Groq invoice extraction failed: {e}. "
                f"Falling back to regex."
            )

    # Regex fallback for invoice numbers (existing behaviour, unchanged)
    for c in _regex_invoice_numbers(state["all_text"]):
        if c not in candidates:
            candidates.append(c)

    logger.info(
        f"[email_id={state['email_id']}] Invoice candidates: {candidates} | "
        f"AR references: {[(r['key_type'], r['value']) for r in candidate_references]}"
    )
    return {
        **state,
        "groq_extracted":          groq_extracted,
        "candidate_invoice_numbers": candidates,
        "candidate_references":    candidate_references,
    }
