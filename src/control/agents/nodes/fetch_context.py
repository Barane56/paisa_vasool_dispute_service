"""
src/control/agents/nodes/fetch_context.py
"""

from __future__ import annotations

import logging
from datetime import UTC

from src.control.agents.state import EmailProcessingState
from src.handlers.http_clients import llm_client
from src.observability import langfuse_context, observe

logger = logging.getLogger(__name__)


@observe(name="node_fetch_context")
async def node_fetch_context(
    state: EmailProcessingState, db_session=None
) -> EmailProcessingState:
    """
    Dispute lookup uses a 4-level fallback chain:
      1. customer_id + invoice_id + dispute_type_name   (most precise)
      2. customer_id + invoice_id                        (type mismatch / re-open)
      3. customer_id + invoice_id IS NULL                (follow-up to a cold mail — links invoice)
      4. customer_id only                                (new cold mail, no invoice in email)

    Memory (episodes, summary, pending questions) is loaded for whatever dispute is found.
    """  # noqa: E501
    if not db_session:
        return {
            **state,
            "invoice_details": None,
            "all_payment_details": [],
            "existing_dispute_id": None,
            "memory_summary": None,
            "recent_episodes": [],
            "pending_questions": [],
        }

    from src.data.repositories.repositories import (
        DisputeRepository,
        InvoiceRepository,
        MemoryEpisodeRepository,
        MemorySummaryRepository,
        OpenQuestionRepository,
        PaymentRepository,
    )

    invoice_details: dict | None = None
    all_payment_details: list[dict] = []
    existing_dispute_id: int | None = None
    memory_summary: str | None = None
    recent_episodes: list[dict] = []
    pending_questions: list[dict] = []

    # ── Invoice details ───────────────────────────────────────────────────────
    if state["matched_invoice_id"]:
        inv_repo = InvoiceRepository(db_session)
        invoice = await inv_repo.get_by_id(state["matched_invoice_id"])
        if invoice:
            db_details = invoice.invoice_details or {}  # type: ignore
            groq_data = state.get("groq_extracted") or {}
            invoice_details = {
                **db_details,
                **{k: v for k, v in groq_data.items() if v is not None},
            }
            # Explicitly hoist line_items to the top level so the LLM prompt
            # can see them directly rather than having to dig through the blob.
            # If line_items is absent or empty, set an explicit marker so the
            # LLM knows the data is missing (not just not shown).
            if not invoice_details.get("line_items"):
                invoice_details["line_items"] = None  # explicit missing signal

    # ── All payment records for this invoice ─────────────────────────────────
    if state.get("matched_payment_ids"):
        pay_repo = PaymentRepository(db_session)
        for pid in state["matched_payment_ids"]:
            payment = await pay_repo.get_by_id(pid)
            if payment and payment.payment_details:
                all_payment_details.append(
                    {
                        "payment_detail_id": payment.payment_detail_id,
                        "invoice_number": payment.invoice_number,
                        **payment.payment_details,
                    }
                )

    # ── Dispute lookup ────────────────────────────────────────────────────────
    # Priority 0: if the task already resolved a dispute (email directly linked
    # to a dispute via EmailInbox.dispute_id), trust it unconditionally.
    # This is more authoritative than L1-L4 — the email thread is already
    # anchored to a specific dispute, regardless of its status.
    customer_id = state.get("customer_id")
    dispute_type_name = state.get("dispute_type_name", "")
    matched_invoice_id = state.get("matched_invoice_id")
    matched_dispute = None
    # L2 Gate B miss: different issue on same invoice — new case + RELATED link
    _l2_related_dispute_id: int | None = None
    _l2_related_dispute_token: str | None = None

    task_dispute_id = state.get("existing_dispute_id")
    if task_dispute_id:
        dispute_repo = DisputeRepository(db_session)
        matched_dispute = await dispute_repo.get_by_id(task_dispute_id)
        if matched_dispute:
            logger.info(
                f"[email_id={state['email_id']}] L0 match: task-level existing_dispute_id="  # noqa: E501
                f"{task_dispute_id} — bypassing L1-L4 matching"
            )
        else:
            logger.warning(
                f"[email_id={state['email_id']}] task existing_dispute_id={task_dispute_id} "  # noqa: E501
                f"not found in DB — falling through to L1-L4"
            )

    if not matched_dispute and customer_id:
        dispute_repo = DisputeRepository(db_session)
        open_disputes = await dispute_repo.get_by_customer(customer_id)

        if open_disputes:
            # Level 1: customer + invoice + dispute type (most precise)
            # Normalise both sides to lowercase + collapsed whitespace so LLM
            # capitalisation variance ("pricing mismatch" vs "Pricing Mismatch")
            # does not cause a miss.
            if matched_invoice_id and dispute_type_name:

                def _norm(s):
                    return " ".join((s or "").lower().split())

                _norm_type = _norm(dispute_type_name)
                for d in open_disputes:
                    if (
                        d.invoice_id == matched_invoice_id
                        and d.dispute_type
                        and _norm(d.dispute_type.reason_name) == _norm_type
                    ):
                        matched_dispute = d
                        logger.info(
                            f"[email_id={state['email_id']}] L1 match: "
                            f"customer+invoice+type → dispute_id={d.dispute_id} "
                            f"(matched '{d.dispute_type.reason_name}' ≈ '{dispute_type_name}')"  # noqa: E501
                        )
                        break

            # Level 2 — smart cold-mail matching
            # -------------------------------------------------------------------
            # Gate A (hard filters — ALL must pass):
            #   1. Same customer + same invoice (baseline)
            #   2. Existing dispute is OPEN or UNDER_REVIEW
            #   3. Dispute was created within 60 days (recency)
            #   4. At least one prior episode exists (real conversation happened)
            #
            # Gate B (semantic similarity):
            #   Embed the incoming body_text (quoted-reply-stripped) and compare
            #   to the existing dispute's description + memory_summary.
            #   ≥ 0.72  → same issue, route as follow-up (existing_dispute_id)
            #   < 0.72  → different issue, new case (related_dispute_id only)
            # -------------------------------------------------------------------
            if not matched_dispute and matched_invoice_id:
                from datetime import datetime, timedelta

                invoice_disputes = [
                    d for d in open_disputes if d.invoice_id == matched_invoice_id
                ]

                if invoice_disputes:
                    # Gate A: filter to OPEN/UNDER_REVIEW + recent + has episodes
                    _now = datetime.now(UTC)
                    _cutoff = _now - timedelta(days=60)

                    gate_a_candidates = []
                    for d in invoice_disputes:
                        if d.status not in ("OPEN", "UNDER_REVIEW"):
                            continue
                        created = d.created_at
                        if created.tzinfo is None:
                            created = created.replace(tzinfo=UTC)
                        if created < _cutoff:
                            continue
                        ep_count = await MemoryEpisodeRepository(
                            db_session
                        ).count_for_dispute(d.dispute_id)  # type: ignore
                        if ep_count == 0:
                            continue
                        gate_a_candidates.append(d)

                    if gate_a_candidates:
                        # Gate B: semantic similarity of incoming body vs dispute description  # noqa: E501
                        gate_b_threshold = 0.72
                        best_candidate = None
                        best_similarity = 0.0

                        # Pre-fetch memory summaries for all Gate A candidates in one pass.  # noqa: E501
                        # memory_summary is the rolling condensed history — essential for  # noqa: E501
                        # mature disputes where the original description is stale.
                        _sum_repo = MemorySummaryRepository(db_session)
                        _candidate_summaries: dict = {}
                        for _cd in gate_a_candidates:
                            try:
                                _sobj = await _sum_repo.get_for_dispute(_cd.dispute_id)  # type: ignore
                                _candidate_summaries[_cd.dispute_id] = (
                                    _sobj.summary_text if _sobj else ""
                                )
                            except Exception:
                                _candidate_summaries[_cd.dispute_id] = ""

                        # body_text is already stripped of quoted reply content
                        # by node_extract_text — using it here ensures we compare
                        # only what the customer actually wrote in this reply.
                        body_to_compare = state.get("body_text", "").strip()
                        if body_to_compare and llm_client:
                            try:
                                incoming_emb = await llm_client.embed(body_to_compare)  # type: ignore
                                if incoming_emb:
                                    for d in gate_a_candidates:
                                        dispute_text = " ".join(
                                            filter(
                                                None,
                                                [
                                                    d.description or "",  # type: ignore
                                                    _candidate_summaries.get(
                                                        d.dispute_id, ""
                                                    ),
                                                ],
                                            )
                                        )
                                        if not dispute_text.strip():
                                            continue
                                        dispute_emb = await llm_client.embed(  # type: ignore
                                            dispute_text
                                        )
                                        if not dispute_emb:
                                            continue
                                        # Cosine similarity
                                        import math

                                        dot = sum(
                                            a * b
                                            for a, b in zip(
                                                incoming_emb, dispute_emb, strict=False
                                            )
                                        )
                                        mag_a = math.sqrt(
                                            sum(a * a for a in incoming_emb)
                                        )
                                        mag_b = math.sqrt(
                                            sum(b * b for b in dispute_emb)
                                        )
                                        sim = (
                                            dot / (mag_a * mag_b)
                                            if mag_a and mag_b
                                            else 0.0
                                        )
                                        if sim > best_similarity:
                                            best_similarity = sim
                                            best_candidate = d
                            except Exception as emb_err:
                                logger.warning(
                                    f"[email_id={state['email_id']}] L2 Gate B embedding "  # noqa: E501
                                    f"failed (non-fatal): {emb_err}"
                                )

                        if best_candidate and best_similarity >= gate_b_threshold:
                            matched_dispute = best_candidate
                            logger.info(
                                f"[email_id={state['email_id']}] L2 match "
                                f"(Gate A+B, similarity={best_similarity:.2f}≥{gate_b_threshold}): "  # noqa: E501
                                f"customer+invoice → dispute_id={matched_dispute.dispute_id} "  # noqa: E501
                                f"(follow-up confirmed)"
                            )
                        else:
                            # Gate B failed — different issue on same invoice.
                            # Pick the most recent Gate A candidate as the related dispute.  # noqa: E501
                            _related = sorted(
                                gate_a_candidates,
                                key=lambda d: d.created_at,  # type: ignore
                                reverse=True,
                            )[0]
                            _related_token = (
                                getattr(_related, "dispute_token", None)
                                or f"PV-{_related.dispute_id:05d}"
                            )
                            logger.info(
                                f"[email_id={state['email_id']}] L2 no match "
                                f"(Gate B similarity={best_similarity:.2f}<{gate_b_threshold}): "  # noqa: E501
                                f"new issue on same invoice — related_dispute_id="
                                f"{_related.dispute_id}, creating new case"
                            )
                            # Store as related (context only, not routing)
                            # Will be written to state and used by generate_response + persist_results  # noqa: E501
                            _l2_related_dispute_id = _related.dispute_id  # type: ignore
                            _l2_related_dispute_token = _related_token

            # Level 3: follow-up to cold mail (dispute exists but had no invoice yet)
            if not matched_dispute and matched_invoice_id:
                for d in open_disputes:
                    if d.invoice_id is None:
                        matched_dispute = d
                        logger.info(
                            f"[email_id={state['email_id']}] L3 match (cold-mail follow-up): "  # noqa: E501
                            f"linking invoice_id={matched_invoice_id} → dispute_id={d.dispute_id}"  # noqa: E501
                        )
                        try:
                            d.invoice_id = matched_invoice_id
                            await db_session.flush()
                        except Exception as patch_err:
                            logger.warning(
                                f"[email_id={state['email_id']}] Could not patch "
                                f"invoice_id on dispute: {patch_err}"
                            )
                        break

            # Level 4: cold mail — no invoice in email
            # Prefer FA_MANUAL disputes since they were created specifically to
            # await a customer response. Fall back to most recently updated.
            if not matched_dispute and not matched_invoice_id:
                fa_disputes = [
                    d
                    for d in open_disputes
                    if getattr(d, "source", "EMAIL") == "FA_MANUAL"
                ]
                matched_dispute = fa_disputes[0] if fa_disputes else open_disputes[0]
                logger.info(
                    f"[email_id={state['email_id']}] L4 match (cold mail): "
                    f"{'FA_MANUAL' if fa_disputes else 'most-recent'} dispute for "
                    f"customer={customer_id} → dispute_id={matched_dispute.dispute_id}"
                )

    # ── Load memory ───────────────────────────────────────────────────────────
    if matched_dispute:
        existing_dispute_id = matched_dispute.dispute_id  # type: ignore

        ep_repo = MemoryEpisodeRepository(db_session)
        recent_eps = await ep_repo.get_latest_n(existing_dispute_id, n=5)  # type: ignore
        recent_episodes = [
            {"actor": ep.actor, "type": ep.episode_type, "text": ep.content_text[:400]}
            for ep in recent_eps
        ]

        sum_repo = MemorySummaryRepository(db_session)
        summary_obj = await sum_repo.get_for_dispute(existing_dispute_id)  # type: ignore
        if summary_obj:
            memory_summary = summary_obj.summary_text  # type: ignore

        q_repo = OpenQuestionRepository(db_session)
        pending_qs = await q_repo.get_pending_for_dispute(existing_dispute_id)  # type: ignore
        pending_questions = [
            {"question_id": q.question_id, "text": q.question_text} for q in pending_qs
        ]

        logger.info(
            f"[email_id={state['email_id']}] Memory loaded for dispute_id={existing_dispute_id}: "  # noqa: E501
            f"{len(recent_episodes)} episodes, {len(pending_questions)} pending questions"  # noqa: E501
        )

    langfuse_context.update_current_observation(
        output={
            "existing_dispute_id": existing_dispute_id,
            "episodes_loaded": len(recent_episodes),
            "pending_questions": len(pending_questions),
            "has_memory_summary": memory_summary is not None,
        }
    )

    # ── AR Document chain — graph query via shared reference keys ───────────
    #
    # Priority:
    #   1. matched_invoice_number → inv_number key (most reliable anchor)
    #   2. candidate_references   → po_number / grn_number / payment_ref / etc.
    #      Tried in order; first non-empty result wins.  Only attempted when
    #      the invoice walk found nothing.
    #
    # The outer try guards against import/setup errors.
    # Each inner try is independent so one bad reference never blocks another.
    ar_document_chain: list = []

    if state.get("customer_id"):
        try:
            from src.core.services.ar_document_service import (
                ARDocumentService,
                resolve_customer_scope,
            )

            ar_svc = ARDocumentService(db_session)
            scope = resolve_customer_scope(state["customer_id"])  # type: ignore

            # Path 1 — invoice number
            if state.get("matched_invoice_number"):
                try:
                    chain = await ar_svc.get_document_chain_for_invoice(
                        invoice_number=state["matched_invoice_number"],  # type: ignore
                        customer_scope=scope,
                    )
                    if chain:
                        ar_document_chain = chain
                        logger.info(
                            f"[email_id={state['email_id']}] AR graph (inv_number): "
                            f"{len(chain)} doc(s) for "
                            f"invoice={state['matched_invoice_number']}: "
                            f"{[d['doc_type'] for d in chain]}"
                        )
                except Exception as inv_walk_err:
                    logger.warning(
                        f"[email_id={state['email_id']}] AR graph inv-walk failed "
                        f"(non-fatal): {inv_walk_err}"
                    )

            # Path 2 — fallback: any other reference extracted from the email
            if not ar_document_chain:
                for ref in state.get("candidate_references") or []:
                    ref_value = (ref.get("value") or "").strip()
                    key_type = (ref.get("key_type") or "").strip()
                    if not ref_value or not key_type:
                        continue
                    try:
                        chain = await ar_svc.get_document_chain_for_reference(
                            ref_value=ref_value,
                            key_type=key_type,
                            customer_scope=scope,
                        )
                        if chain:
                            ar_document_chain = chain
                            logger.info(
                                f"[email_id={state['email_id']}] AR graph "
                                f"({key_type}={ref_value}): {len(chain)} doc(s): "
                                f"{[d['doc_type'] for d in chain]}"
                            )
                            break  # first hit wins
                    except Exception as ref_walk_err:
                        logger.warning(
                            f"[email_id={state['email_id']}] AR graph ref-walk failed "
                            f"for {key_type}={ref_value!r} (non-fatal): {ref_walk_err}"
                        )
                        # continue to next reference

        except Exception as ar_outer_err:
            logger.warning(
                f"[email_id={state['email_id']}] AR graph setup failed "
                f"(non-fatal): {ar_outer_err}"
            )

    return {
        **state,
        "invoice_details": invoice_details,
        "all_payment_details": all_payment_details,
        "existing_dispute_id": existing_dispute_id,
        "memory_summary": memory_summary,
        "recent_episodes": recent_episodes,
        "pending_questions": pending_questions,
        "ar_document_chain": ar_document_chain,
        "related_dispute_id": _l2_related_dispute_id,
        "related_dispute_token": _l2_related_dispute_token,
    }
