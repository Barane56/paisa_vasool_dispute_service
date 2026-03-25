"""
src/control/agents/nodes/extract_invoice.py
"""

from __future__ import annotations

import logging

from src.control.agents.nodes.extract_text import _regex_invoice_numbers
from src.control.agents.state import EmailProcessingState
from src.observability import langfuse_context, observe

logger = logging.getLogger(__name__)

# Maps groq_extracted field names → ar_document_key key_type values
_REF_FIELD_TO_KEY_TYPE: dict[str, str] = {
    "po_number": "po_number",
    "grn_number": "grn_number",
    "payment_ref": "payment_ref",
    "contract_number": "contract_number",
}


def _parse_invoice_list(raw: dict) -> list[dict]:
    """
    Parse the LLM response into a list of invoice dicts.

    Handles two shapes:
      New format: {"invoices": [{...}, {...}]}
      Old format: {"invoice_number": "...", ...}  (single flat object)

    Returns a non-empty list of invoice dicts, or [] if nothing usable.
    """
    if not raw or not isinstance(raw, dict):
        return []

    invoices = raw.get("invoices")
    if isinstance(invoices, list) and invoices:
        # New format — filter to dicts only
        return [inv for inv in invoices if isinstance(inv, dict)]

    # Old flat format — wrap in list for uniform handling
    if raw.get("invoice_number") or raw.get("po_number"):
        return [raw]

    return []


@observe(name="node_extract_invoice_data")
async def node_extract_invoice_data_via_groq(
    state: EmailProcessingState, llm_client=None
) -> EmailProcessingState:
    groq_extracted: dict | None = None
    candidates: list[str] = []
    candidate_references: list[dict] = []

    if llm_client:
        try:
            raw_response = await llm_client.extract_invoice_data(
                state["all_text"],
                attachment_metadata=state.get("attachment_metadata"),
            )

            invoice_list = _parse_invoice_list(raw_response)

            if invoice_list:
                # Store the first invoice in groq_extracted for backward
                # compatibility — downstream prompts (structure_email) use it
                # for context on the primary invoice.
                groq_extracted = invoice_list[0]

                # Collect invoice numbers and AR references from ALL invoices
                seen_inv: set[str] = set()
                seen_ref: set[tuple] = set()

                for inv in invoice_list:
                    # ── Invoice number ────────────────────────────────────────
                    inv_num = (inv.get("invoice_number") or "").strip().upper()
                    if inv_num and inv_num not in seen_inv:
                        seen_inv.add(inv_num)
                        candidates.append(inv_num)

                    # ── PO number: candidate invoice AND reference ─────────────
                    po = (inv.get("po_number") or "").strip().upper()
                    if po:
                        if po not in seen_inv:
                            seen_inv.add(po)
                            candidates.append(po)
                        ref_key = ("po_number", po)
                        if ref_key not in seen_ref:
                            seen_ref.add(ref_key)
                            candidate_references.append(
                                {"value": po, "key_type": "po_number"}
                            )

                    # ── Other AR references ────────────────────────────────────
                    for field, key_type in _REF_FIELD_TO_KEY_TYPE.items():
                        if field == "po_number":
                            continue
                        raw_val = (inv.get(field) or "").strip().upper()
                        if raw_val:
                            ref_key = (key_type, raw_val)
                            if ref_key not in seen_ref:
                                seen_ref.add(ref_key)
                                candidate_references.append(
                                    {"value": raw_val, "key_type": key_type}
                                )

                langfuse_context.update_current_observation(
                    input={"text_length": len(state["all_text"])},
                    output={
                        "invoice_count": len(invoice_list),
                        "invoice_numbers": [
                            inv.get("invoice_number") for inv in invoice_list
                        ],
                    },
                )
                logger.info(
                    f"[email_id={state['email_id']}] Invoice extraction succeeded. "
                    f"Found {len(invoice_list)} invoice(s): "
                    f"{[inv.get('invoice_number') for inv in invoice_list]}"
                )
            else:
                logger.warning(
                    f"[email_id={state['email_id']}] Invoice extraction returned no invoices."
                )

        except Exception as e:
            logger.warning(
                f"[email_id={state['email_id']}] Groq invoice extraction failed: {e}. "
                f"Falling back to regex."
            )

    # Regex fallback for invoice numbers (runs always, deduplicates against LLM results)
    for c in _regex_invoice_numbers(state["all_text"]):
        if c not in candidates:
            candidates.append(c)

    logger.info(
        f"[email_id={state['email_id']}] Invoice candidates: {candidates} | "
        f"AR references: {[(r['key_type'], r['value']) for r in candidate_references]}"
    )

    return {
        **state,
        "groq_extracted": groq_extracted,
        "candidate_invoice_numbers": candidates,
        "candidate_references": candidate_references,
    }
