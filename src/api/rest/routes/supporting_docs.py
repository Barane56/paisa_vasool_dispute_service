"""
Supporting Documents endpoint
==============================
Uses the existing `analysis_supporting_refs` table to store and retrieve
documents (payments, invoices, email attachments, etc.) that support a
dispute's AI analysis.

Table schema reminder:
    analysis_supporting_refs
        ref_id          SERIAL PK
        analysis_id     FK → dispute_ai_analysis.analysis_id
        reference_table TEXT   (e.g. 'payment_detail', 'invoice_data', 'email_attachments')
        ref_id_value    INT    (PK of the referenced row)
        context_note    TEXT   (why this document is relevant)
"""
from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from src.data.clients.postgres import get_db
from src.data.repositories.repositories import (
    AnalysisSupportingRefRepository,
    DisputeAIAnalysisRepository,
    DisputeRepository,
)
from src.api.rest.dependencies import get_current_user
from src.schemas.schemas import (
    CurrentUser,
    SupportingRefCreate,
    SupportingRefResponse,
    SupportingRefListResponse,
    SuccessResponse,
)
from src.core.exceptions import DisputeNotFoundError, AnalysisNotFoundError

router = APIRouter(prefix="/disputes", tags=["Supporting Documents"])


@router.get("/{dispute_id}/supporting-docs", response_model=SupportingRefListResponse)
async def list_supporting_docs(
    dispute_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Return all supporting document references attached to any analysis of the given dispute.
    """
    dispute_repo = DisputeRepository(db)
    dispute = await dispute_repo.get_by_id(dispute_id)
    if not dispute:
        raise DisputeNotFoundError(dispute_id)

    ref_repo = AnalysisSupportingRefRepository(db)
    items = await ref_repo.get_by_dispute_via_analysis(dispute_id)
    return SupportingRefListResponse(
        dispute_id=dispute_id,
        total=len(items),
        items=items,
    )


@router.post(
    "/{dispute_id}/supporting-docs",
    response_model=SupportingRefResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_supporting_doc(
    dispute_id: int,
    data: SupportingRefCreate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Attach a supporting document to a dispute analysis.

    The `analysis_id` must belong to the dispute.
    If an identical (analysis_id, reference_table, ref_id_value) row already exists,
    its `context_note` is updated instead of creating a duplicate.

    **Common reference_table values**:
    - `payment_detail` — a payment record
    - `invoice_data` — an invoice record
    - `email_attachments` — a PDF attachment from an email
    - `email_inbox` — a raw email
    """
    # Verify the dispute exists
    dispute_repo = DisputeRepository(db)
    dispute = await dispute_repo.get_by_id(dispute_id)
    if not dispute:
        raise DisputeNotFoundError(dispute_id)

    # Verify the analysis belongs to this dispute
    analysis_repo = DisputeAIAnalysisRepository(db)
    analyses = await analysis_repo.get_all_for_dispute(dispute_id)
    analysis_ids = {a.analysis_id for a in analyses}
    if data.analysis_id not in analysis_ids:
        raise AnalysisNotFoundError(data.analysis_id)

    ref_repo = AnalysisSupportingRefRepository(db)
    ref = await ref_repo.upsert_supporting_doc(
        analysis_id=data.analysis_id,
        reference_table=data.reference_table,
        ref_id_value=data.ref_id_value,
        context_note=data.context_note,
    )
    await db.commit()
    await db.refresh(ref)
    return ref


@router.delete(
    "/{dispute_id}/supporting-docs/{ref_id}",
    response_model=SuccessResponse,
)
async def remove_supporting_doc(
    dispute_id: int,
    ref_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Remove a supporting document reference from a dispute analysis.
    """
    dispute_repo = DisputeRepository(db)
    dispute = await dispute_repo.get_by_id(dispute_id)
    if not dispute:
        raise DisputeNotFoundError(dispute_id)

    ref_repo = AnalysisSupportingRefRepository(db)
    deleted = await ref_repo.delete_ref(ref_id)
    if not deleted:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Supporting ref {ref_id} not found")

    await db.commit()
    return SuccessResponse(message=f"Supporting document ref {ref_id} removed")
