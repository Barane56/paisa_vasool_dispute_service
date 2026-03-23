"""
src/data/repositories/ar_document_repository.py
================================================
Repository for AR document graph — ar_documents + ar_document_keys.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select, and_, or_, distinct
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.data.models.postgres.ar_document_models import ARDocument, ARDocumentKey

logger = logging.getLogger(__name__)


class ARDocumentRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ── Create ────────────────────────────────────────────────────────────────

    async def create_document(
        self,
        customer_scope: str,
        doc_type: str,
        doc_date,
        file_path: Optional[str],
        raw_text: Optional[str],
        uploaded_by: Optional[int],
    ) -> ARDocument:
        doc = ARDocument(
            customer_scope=customer_scope,
            doc_type=doc_type,
            doc_date=doc_date,
            file_path=file_path,
            raw_text=raw_text,
            uploaded_by=uploaded_by,
            status="ACTIVE",
        )
        self.db.add(doc)
        await self.db.flush()
        return doc

    async def upsert_keys(self, doc_id: int, keys: list) -> list[ARDocumentKey]:
        """
        Insert extracted keys. Silently skip duplicates (same doc+type+norm).
        keys: list of ExtractedKey dataclass instances.
        """
        inserted = []
        for ek in keys:
            # Check for existing key with same type+norm on this doc
            existing = (await self.db.execute(
                select(ARDocumentKey).where(
                    and_(
                        ARDocumentKey.doc_id        == doc_id,
                        ARDocumentKey.key_type       == ek.key_type,
                        ARDocumentKey.key_value_norm == ek.key_value_norm,
                    )
                )
            )).scalar_one_or_none()

            if existing:
                # Update confidence if new extraction is more confident
                if ek.confidence > existing.confidence:
                    existing.confidence = ek.confidence
                    existing.source     = ek.source
                inserted.append(existing)
                continue

            key = ARDocumentKey(
                doc_id         = doc_id,
                key_type       = ek.key_type,
                key_value_raw  = ek.key_value_raw,
                key_value_norm = ek.key_value_norm,
                confidence     = ek.confidence,
                source         = ek.source,
                verified       = False,
            )
            self.db.add(key)
            inserted.append(key)

        await self.db.flush()
        return inserted

    # ── Query ─────────────────────────────────────────────────────────────────

    async def get_by_id(self, doc_id: int) -> Optional[ARDocument]:
        return (await self.db.execute(
            select(ARDocument)
            .options(selectinload(ARDocument.keys))
            .where(ARDocument.doc_id == doc_id)
        )).scalar_one_or_none()

    async def get_related_documents(
        self,
        doc_id: int,
        customer_scope: Optional[str] = None,
    ) -> list[dict]:
        """
        Single-hop traversal anchored on an INVOICE or PO document.

        Algorithm:
          1. Load the starting document (doc_id).
          2. If it is already an INVOICE or PO, use it as the anchor.
             Otherwise scan its keys for an inv_number or po_number that
             points to a connected INVOICE or PO (pivot step) — e.g. a GRN
             or PAYMENT that carries an inv_number key pivots to the invoice.
             Falls back to doc_id itself if no pivot found.
          3. From the anchor, do ONE hop: find every document that shares an
             inv_number OR po_number key with the anchor.

        Only inv_number and po_number are used for traversal:
          - They are transaction-specific (1-to-few cardinality).
          - payment_ref is wide — one payment can cover many invoices/POs.
          - grn_number / contract_number are valid starting anchors but not
            useful traversal keys for finding the full cluster.

        The anchor document itself is excluded from results.
        customer_scope is enforced on the batch-load to prevent cross-customer
        data leakage.

        Returns list of dicts:
          {document, shared_keys: [{key_type, key_value_norm, key_value_raw}]}
        """
        ANCHOR_TYPES    = {"INVOICE", "PO"}
        TRAVERSAL_TYPES = {"po_number", "inv_number"}

        # Step 1: load starting document with its keys
        start_doc = (await self.db.execute(
            select(ARDocument)
            .options(selectinload(ARDocument.keys))
            .where(ARDocument.doc_id == doc_id)
        )).scalar_one_or_none()

        if not start_doc:
            return []

        # Step 2: find the INVOICE/PO anchor
        if start_doc.doc_type in ANCHOR_TYPES:
            anchor_doc = start_doc
        else:
            anchor_doc = None
            pivot_keys = [
                k for k in (start_doc.keys or [])
                if k.key_type in TRAVERSAL_TYPES
            ]
            for pk in pivot_keys:
                candidate_key = (await self.db.execute(
                    select(ARDocumentKey)
                    .join(ARDocument, ARDocument.doc_id == ARDocumentKey.doc_id)
                    .where(
                        and_(
                            ARDocumentKey.key_type       == pk.key_type,
                            ARDocumentKey.key_value_norm == pk.key_value_norm,
                            ARDocumentKey.doc_id         != doc_id,
                            ARDocument.doc_type.in_(ANCHOR_TYPES),
                            *([ARDocument.customer_scope == customer_scope]
                              if customer_scope else []),
                        )
                    )
                    .order_by(ARDocument.created_at.desc())
                    .limit(1)
                )).scalar_one_or_none()

                if candidate_key:
                    anchor_doc = (await self.db.execute(
                        select(ARDocument)
                        .options(selectinload(ARDocument.keys))
                        .where(ARDocument.doc_id == candidate_key.doc_id)
                    )).scalar_one_or_none()
                    if anchor_doc:
                        logger.debug(
                            f"get_related_documents: pivoted from "
                            f"doc_id={doc_id} ({start_doc.doc_type}) "
                            f"→ anchor doc_id={anchor_doc.doc_id} "
                            f"({anchor_doc.doc_type}) via "
                            f"{pk.key_type}={pk.key_value_raw}"
                        )
                        break

            if not anchor_doc:
                anchor_doc = start_doc

        # Step 3: single hop from anchor via inv_number / po_number only
        anchor_keys = [
            k for k in (anchor_doc.keys or [])
            if k.key_type in TRAVERSAL_TYPES
        ]
        if not anchor_keys:
            return []

        collected: dict[int, dict] = {}

        for ak in anchor_keys:
            matching = (await self.db.execute(
                select(ARDocumentKey).where(
                    and_(
                        ARDocumentKey.key_type       == ak.key_type,
                        ARDocumentKey.key_value_norm == ak.key_value_norm,
                        ARDocumentKey.doc_id         != anchor_doc.doc_id,
                    )
                )
            )).scalars().all()

            for mk in matching:
                other_id = mk.doc_id
                if other_id not in collected:
                    collected[other_id] = {"shared_keys": []}
                collected[other_id]["shared_keys"].append({
                    "key_type":       ak.key_type,
                    "key_value_norm": ak.key_value_norm,
                    "key_value_raw":  ak.key_value_raw,
                })

        if not collected:
            return []

        scope_filter = (
            [ARDocument.customer_scope == customer_scope]
            if customer_scope else []
        )
        related_docs = (await self.db.execute(
            select(ARDocument)
            .options(selectinload(ARDocument.keys))
            .where(
                and_(
                    ARDocument.doc_id.in_(list(collected.keys())),
                    *scope_filter,
                )
            )
        )).scalars().all()

        result = []
        for doc in related_docs:
            result.append({
                "document":    doc,
                "shared_keys": collected[doc.doc_id]["shared_keys"],
            })

        result.sort(key=lambda x: (x["document"].doc_date or x["document"].created_at))
        return result

    async def get_documents_for_customer(
        self,
        customer_scope: str,
        doc_type: Optional[str] = None,
        limit: int = 50,
    ) -> list[ARDocument]:
        q = (
            select(ARDocument)
            .options(selectinload(ARDocument.keys))
            .where(ARDocument.customer_scope == customer_scope)
            .order_by(ARDocument.created_at.desc())
            .limit(limit)
        )
        if doc_type:
            q = q.where(ARDocument.doc_type == doc_type)
        return list((await self.db.execute(q)).scalars().all())

    async def get_chain_for_invoice(
        self,
        invoice_number_norm: str,
        customer_scope: str,
    ) -> list[dict]:
        """
        Find the invoice document by inv_number key, then return its full graph.
        Used by the agent pipeline to inject document chain into LLM context.
        """
        return await self.get_chain_for_reference(
            key_value_norm = invoice_number_norm,
            key_type       = "inv_number",
            customer_scope = customer_scope,
        )

    async def get_chain_for_reference(
        self,
        key_value_norm: str,
        key_type:       str,
        customer_scope: str,
    ) -> list[dict]:
        """
        Generic graph walk starting from ANY reference key type
        (inv_number, po_number, grn_number, payment_ref, contract_number,
        credit_note_number).

        Finds the AR document that carries this key, then returns its full
        connected graph via get_related_documents.  If multiple documents
        share the same key value (rare but possible), the most recently
        created one is used as the walk root.

        Returns [] when no document carries this key.
        """
        VALID_KEY_TYPES = {
            "inv_number", "po_number", "grn_number",
            "payment_ref", "contract_number", "credit_note_number",
        }
        if key_type not in VALID_KEY_TYPES:
            logger.warning(
                f"get_chain_for_reference: unknown key_type={key_type!r} — skipping"
            )
            return []

        if not key_value_norm or not key_value_norm.strip():
            return []

        # Find the anchor document for this reference key
        anchor_key = (await self.db.execute(
            select(ARDocumentKey)
            .join(ARDocument, ARDocument.doc_id == ARDocumentKey.doc_id)
            .where(
                and_(
                    ARDocumentKey.key_type       == key_type,
                    ARDocumentKey.key_value_norm == key_value_norm,
                    ARDocument.customer_scope    == customer_scope,
                )
            )
            .order_by(ARDocument.created_at.desc())
            .limit(1)
        )).scalar_one_or_none()

        if not anchor_key:
            return []

        return await self.get_related_documents(
            doc_id         = anchor_key.doc_id,
            customer_scope = customer_scope,
        )

    async def add_manual_key(
        self,
        doc_id: int,
        key_type: str,
        key_value_raw: str,
    ) -> ARDocumentKey:
        """FA manually adds or corrects a key."""
        from src.core.services.key_extraction_service import normalize_ref
        norm = normalize_ref(key_value_raw)
        key = ARDocumentKey(
            doc_id         = doc_id,
            key_type       = key_type,
            key_value_raw  = key_value_raw,
            key_value_norm = norm,
            confidence     = 1.0,
            source         = "manual",
            verified       = True,
        )
        self.db.add(key)
        await self.db.flush()
        return key
