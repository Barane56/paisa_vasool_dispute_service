"""
src/core/services/ar_document_service.py
"""
from __future__ import annotations
import logging, re, uuid
from pathlib import Path
from typing import Optional
from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from src.config.settings import settings
from src.core.services.key_extraction_service import extract_document_keys, normalize_ref
from src.data.repositories.ar_document_repository import ARDocumentRepository

logger = logging.getLogger(__name__)
STORAGE_DIR     = Path(getattr(settings, "ATTACHMENT_STORAGE_DIR", "/tmp/dispute_attachments"))
GCS_PREFIX      = "gcs:"    # file_path starts with this when stored in GCS
LOCAL_PREFIX    = "local:"  # file_path starts with this when stored on local disk
VALID_DOC_TYPES = {"PO", "INVOICE", "GRN", "PAYMENT", "CONTRACT", "CREDIT_NOTE"}

def _safe_filename(name: str) -> str:
    return re.sub(r"[^\w.\-]", "_", name)[:100]

def _extract_text(file_bytes: bytes, content_type: str, filename: str) -> str:
    try:
        if content_type == "application/pdf" or filename.lower().endswith(".pdf"):
            try:
                import io
                from pypdf import PdfReader
                reader = PdfReader(io.BytesIO(file_bytes))
                return "\n\n".join(p.extract_text() or "" for p in reader.pages).strip()
            except Exception as e:
                logger.warning(f"PDF extraction failed for {filename}: {e}")
                return ""
        if (content_type and content_type.startswith("text/")) or \
           Path(filename).suffix.lower() in (".txt", ".csv", ".xml", ".json"):
            return file_bytes.decode("utf-8", errors="replace")
        return ""
    except Exception as e:
        logger.error(f"Text extraction error: {e}")
        return ""

def _store_local(file_bytes: bytes, safe_name: str) -> str:
    """Save bytes to local filesystem, return LOCAL_PREFIX path for DB storage."""
    local_dir = STORAGE_DIR / "ar_documents"
    local_dir.mkdir(parents=True, exist_ok=True)
    unique = f"{uuid.uuid4().hex}_{safe_name}"
    (local_dir / unique).write_bytes(file_bytes)
    return f"{LOCAL_PREFIX}ar_documents/{unique}"

def _local_full_path(file_path: str) -> Path:
    """Convert a LOCAL_PREFIX path back to an absolute filesystem path."""
    rel = file_path.removeprefix(LOCAL_PREFIX)
    return STORAGE_DIR / rel

def resolve_customer_scope(customer_email: str) -> str:
    if not customer_email or "@" not in customer_email:
        return customer_email or "unknown"
    return customer_email.lower().strip()

class ARDocumentService:
    def __init__(self, db: AsyncSession):
        self.db   = db
        self.repo = ARDocumentRepository(db)

    async def upload_document(self, file: UploadFile, doc_type: str,
                               customer_scope: str, doc_date: Optional[str],
                               uploaded_by: int) -> dict:
        if doc_type not in VALID_DOC_TYPES:
            raise ValueError(f"Invalid doc_type '{doc_type}'.")
        file_bytes  = await file.read()
        safe_name   = _safe_filename(file.filename or "upload")
        raw_text    = _extract_text(file_bytes, file.content_type or "", safe_name)

        # ── Storage: try GCS, fall back to local ─────────────────────────────
        file_path: str
        if settings.GCS_ENABLED:
            try:
                from src.core.services.gcs_service import async_upload_attachment, GCSUnavailable
                gcs_path  = await async_upload_attachment(
                    file_bytes = file_bytes,
                    filename   = safe_name,
                    folder     = "ar_documents",
                )
                file_path = f"{GCS_PREFIX}{gcs_path}"
                logger.info(f"[ar_upload] stored in GCS: {gcs_path}")
            except (Exception,) as gcs_err:
                logger.warning(
                    f"[ar_upload] GCS upload failed, falling back to local: {gcs_err}"
                )
                file_path = _store_local(file_bytes, safe_name)
                logger.info(f"[ar_upload] stored locally (GCS fallback): {file_path}")
        else:
            file_path = _store_local(file_bytes, safe_name)
            logger.info(f"[ar_upload] stored locally (GCS disabled): {file_path}")

        parsed_date = None
        if doc_date:
            try:
                from datetime import date
                parsed_date = date.fromisoformat(doc_date)
            except ValueError:
                pass

        doc       = await self.repo.create_document(customer_scope, doc_type, parsed_date,
                                                     file_path, raw_text, uploaded_by)
        extracted = await extract_document_keys(raw_text, doc_type)
        keys      = await self.repo.upsert_keys(doc.doc_id, extracted)
        logger.info(f"[ar_upload] doc_id={doc.doc_id} keys={[k.key_type for k in keys]}")
        await self.db.commit()
        related = await self.repo.get_related_documents(doc.doc_id, customer_scope)
        return self._fmt_response(doc, keys, related)

    async def get_file_bytes(self, doc_id: int) -> tuple[bytes, str]:
        """
        Fetch raw file bytes for a given AR document, handling both GCS and local storage.
        Returns (bytes, safe_filename).
        Raises ValueError if the document does not exist.
        Raises FileNotFoundError if the file cannot be retrieved from storage.
        """
        doc = await self.repo.get_by_id(doc_id)
        if not doc:
            raise ValueError(f"AR document {doc_id} not found")

        file_path = doc.file_path or ""
        # Derive a clean filename from whatever was stored
        safe_name = Path(file_path.split("/")[-1]).name if file_path else f"ar_doc_{doc_id}"

        if file_path.startswith(GCS_PREFIX):
            gcs_path = file_path.removeprefix(GCS_PREFIX)
            try:
                from src.core.services.gcs_service import async_download_attachment
                data = await async_download_attachment(gcs_path)
                logger.info(f"[ar_download] GCS fetch: {gcs_path} ({len(data)} bytes)")
                return data, safe_name
            except Exception as exc:
                logger.error(f"[ar_download] GCS fetch failed for doc_id={doc_id}: {exc}")
                raise FileNotFoundError(f"Could not retrieve file from GCS: {exc}") from exc

        if file_path.startswith(LOCAL_PREFIX):
            abs_path = _local_full_path(file_path)
            if not abs_path.exists():
                raise FileNotFoundError(f"Local file not found on disk: {abs_path}")
            data = abs_path.read_bytes()
            logger.info(f"[ar_download] local fetch: {abs_path} ({len(data)} bytes)")
            return data, abs_path.name

        raise FileNotFoundError(f"Unrecognised file_path format for doc_id={doc_id}: {file_path!r}")

    async def get_signed_url_if_gcs(self, doc_id: int, expiry_minutes: int = 30) -> Optional[str]:
        """
        If the document is stored in GCS and ADC is available, return a signed URL.
        Returns None for locally stored files or when GCS credentials aren't available.
        Used by the download endpoint to redirect instead of proxying the bytes.
        """
        doc = await self.repo.get_by_id(doc_id)
        if not doc or not (doc.file_path or "").startswith(GCS_PREFIX):
            return None
        gcs_path = doc.file_path.removeprefix(GCS_PREFIX)
        try:
            from src.core.services.gcs_service import async_get_signed_url, GCSUnavailable
            return await async_get_signed_url(gcs_path, expiry_minutes=expiry_minutes)
        except Exception as exc:
            logger.warning(
                f"[ar_download] Signed URL unavailable for doc_id={doc_id} "
                f"(will stream instead): {exc}"
            )
            return None

    async def get_document(self, doc_id: int) -> Optional[dict]:
        doc = await self.repo.get_by_id(doc_id)
        if not doc:
            return None
        related = await self.repo.get_related_documents(doc.doc_id, doc.customer_scope)
        return self._fmt_response(doc, list(doc.keys), related)

    async def get_related(self, doc_id: int) -> list[dict]:
        doc = await self.repo.get_by_id(doc_id)
        if not doc:
            return []
        return [self._fmt_related(r)
                for r in await self.repo.get_related_documents(doc.doc_id, doc.customer_scope)]

    async def add_manual_key(self, doc_id: int, key_type: str,
                              key_value_raw: str, current_user: int) -> dict:
        key = await self.repo.add_manual_key(doc_id, key_type, key_value_raw)
        await self.db.commit()
        return {"key_id": key.key_id, "key_type": key.key_type,
                "key_value_raw": key.key_value_raw, "key_value_norm": key.key_value_norm,
                "confidence": key.confidence, "source": key.source, "verified": key.verified}

    async def list_for_customer(self, customer_scope: str) -> list[dict]:
        """
        Return all AR documents for a customer scope, including their extracted keys.
        Used by the FA manual case creation picker and the dispute docs tab.
        Returns ARDocRelated-shape (shared_keys=[] since these are anchor docs).
        """
        docs = await self.repo.get_documents_for_customer(customer_scope)
        return [
            {
                **self._fmt_doc(d),
                "shared_keys": [],
                "all_keys": [self._fmt_key(k) for k in (d.keys or [])],
            }
            for d in docs
        ]

    async def get_document_chain_for_invoice(self, invoice_number: str,
                                              customer_scope: str) -> list[dict]:
        norm    = normalize_ref(invoice_number)
        related = await self.repo.get_chain_for_invoice(norm, customer_scope)
        # Also include the anchor invoice document itself at position 0
        # so the chain contains: [INVOICE, PO, GRN, PAYMENT, ...]
        inv_key = None
        try:
            from sqlalchemy import select, and_
            from src.data.models.postgres.ar_document_models import ARDocumentKey, ARDocument
            from sqlalchemy.orm import selectinload
            inv_key_row = (await self.db.execute(
                select(ARDocumentKey)
                .join(ARDocument, ARDocument.doc_id == ARDocumentKey.doc_id)
                .where(
                    and_(
                        ARDocumentKey.key_type       == "inv_number",
                        ARDocumentKey.key_value_norm == norm,
                        ARDocument.customer_scope    == customer_scope,
                    )
                )
                .limit(1)
            )).scalar_one_or_none()
            if inv_key_row:
                anchor_doc = (await self.db.execute(
                    select(ARDocument)
                    .options(selectinload(ARDocument.keys))
                    .where(ARDocument.doc_id == inv_key_row.doc_id)
                )).scalar_one_or_none()
                if anchor_doc:
                    inv_key = self._fmt_related({"document": anchor_doc, "shared_keys": []})
        except Exception:
            pass
        chain = [self._fmt_related(r) for r in related]
        return ([inv_key] + chain) if inv_key else chain

    async def get_document_chain_for_reference(
        self,
        ref_value:     str,
        key_type:      str,
        customer_scope: str,
    ) -> list[dict]:
        """
        Generic entry-point: walk the AR document graph starting from any
        reference type — po_number, grn_number, payment_ref, etc.
        Includes the anchor document itself as the first item in the chain.
        Returns [] when nothing is found — never raises.
        """
        if not ref_value or not ref_value.strip():
            return []
        norm    = normalize_ref(ref_value)
        related = await self.repo.get_chain_for_reference(
            key_value_norm = norm,
            key_type       = key_type,
            customer_scope = customer_scope,
        )
        # Include the anchor document itself
        try:
            from sqlalchemy import select, and_
            from src.data.models.postgres.ar_document_models import ARDocumentKey, ARDocument
            from sqlalchemy.orm import selectinload
            anchor_key_row = (await self.db.execute(
                select(ARDocumentKey)
                .join(ARDocument, ARDocument.doc_id == ARDocumentKey.doc_id)
                .where(
                    and_(
                        ARDocumentKey.key_type       == key_type,
                        ARDocumentKey.key_value_norm == norm,
                        ARDocument.customer_scope    == customer_scope,
                    )
                )
                .order_by(ARDocument.created_at.desc())
                .limit(1)
            )).scalar_one_or_none()
            if anchor_key_row:
                anchor_doc = (await self.db.execute(
                    select(ARDocument)
                    .options(selectinload(ARDocument.keys))
                    .where(ARDocument.doc_id == anchor_key_row.doc_id)
                )).scalar_one_or_none()
                if anchor_doc:
                    anchor_fmt = self._fmt_related({"document": anchor_doc, "shared_keys": []})
                    chain = [self._fmt_related(r) for r in related]
                    return [anchor_fmt] + chain
        except Exception:
            pass
        return [self._fmt_related(r) for r in related]

    async def get_document_chain_for_doc_id(self, doc_id: int,
                                             customer_scope: str) -> list[dict]:
        """
        Fetch the full graph chain starting from a known AR document ID.
        Used when the FA selects a specific document during manual case creation —
        we already have the doc_id so we skip the invoice-number key lookup.
        customer_scope is used to scope the related-document walk to the correct
        customer (same scoping rule as invoice-driven lookups).
        """
        doc = await self.repo.get_by_id(doc_id)
        if not doc:
            return []
        # Override with the provided scope so FA-selected docs are always scoped
        # to the customer being disputed, not the doc's stored scope (they should
        # match but this is a safety guard).
        effective_scope = customer_scope or doc.customer_scope
        related = await self.repo.get_related_documents(doc.doc_id, effective_scope)
        # Also include the anchor doc itself as the first item in the chain
        anchor = self._fmt_related({"document": doc, "shared_keys": []})
        return [anchor] + [self._fmt_related(r) for r in related]

    def _fmt_key(self, k) -> dict:
        return {"key_id": k.key_id, "key_type": k.key_type,
                "key_value_raw": k.key_value_raw, "key_value_norm": k.key_value_norm,
                "confidence": round(k.confidence, 2), "source": k.source, "verified": k.verified}

    def _fmt_doc(self, doc) -> dict:
        return {"doc_id": doc.doc_id, "doc_type": doc.doc_type,
                "customer_scope": doc.customer_scope,
                "doc_date": doc.doc_date.isoformat() if doc.doc_date else None,
                "status": doc.status, "created_at": doc.created_at.isoformat(),
                "has_file": bool(doc.file_path),
                "download_url": f"/dispute/api/v1/ar-documents/{doc.doc_id}/download"}

    def _fmt_related(self, r: dict) -> dict:
        doc = r["document"]
        return {**self._fmt_doc(doc), "shared_keys": r["shared_keys"],
                "all_keys": [self._fmt_key(k) for k in (doc.keys or [])]}

    def _fmt_response(self, doc, keys: list, related: list) -> dict:
        return {
            "document": self._fmt_doc(doc),
            "extracted_keys": [self._fmt_key(k) for k in keys],
            "related_documents": [self._fmt_related(r) for r in related],
            "graph_summary": {
                "total_keys_extracted": len(keys),
                "related_docs_found": len(related),
                "linked_types": list({r["document"].doc_type for r in related}),
            },
        }

    # ── Dispute ↔ AR document linking ────────────────────────────────────────

    async def link_ar_documents_to_dispute(
        self,
        dispute_id:   int,
        doc_ids:      list[int],
        linked_by:    int | None = None,
        context_note: str | None = None,
    ) -> None:
        """
        Upsert rows in dispute_ar_documents for each doc_id.
        Silently skips duplicates (uq_dispute_ar_doc constraint).
        Called by the email pipeline (agent-linked, linked_by=None)
        and manual FA case creation (linked_by=user_id).
        """
        from src.data.models.postgres.ar_document_models import DisputeARDocument

        for doc_id in doc_ids:
            # Use a savepoint so a duplicate-key violation only rolls back this
            # single insert and never kills the surrounding transaction.
            # A full self.db.rollback() here would wipe every unflushed row in
            # the caller's transaction (e.g. EmailInbox, DisputeMaster) and
            # cause FK violations on subsequent inserts.
            try:
                async with self.db.begin_nested():
                    row = DisputeARDocument(
                        dispute_id   = dispute_id,
                        doc_id       = doc_id,
                        linked_by    = linked_by,
                        context_note = context_note,
                    )
                    self.db.add(row)
                    # flush happens automatically when the savepoint block exits
            except Exception:
                # Savepoint was rolled back — duplicate or other constraint
                # violation. Outer transaction is intact. Safe to continue.
                pass

    async def replace_anchor_document(
        self,
        dispute_id:    int,
        new_doc_id:    int,
        user_id:       int,
        customer_scope: str,
    ) -> list[dict]:
        """
        Replace all AR documents linked to a dispute with the graph chain
        rooted at new_doc_id.

        Steps:
          1. Verify new_doc_id belongs to customer_scope (security check).
          2. Delete all current dispute_ar_documents rows for this dispute.
          3. Walk the graph from new_doc_id to get the full chain.
          4. Write new dispute_ar_documents rows for each doc in the chain.
          5. Return the new linked doc list (same shape as get_ar_documents_for_dispute).
        """
        from src.data.models.postgres.ar_document_models import DisputeARDocument, ARDocument
        from sqlalchemy import select, delete as sa_delete

        # Security: ensure the selected doc belongs to this customer
        anchor = (await self.db.execute(
            select(ARDocument).where(
                ARDocument.doc_id        == new_doc_id,
                ARDocument.customer_scope == customer_scope,
            )
        )).scalar_one_or_none()
        if not anchor:
            raise ValueError(
                f"Document {new_doc_id} not found for customer scope '{customer_scope}'"
            )

        # Clear current links for this dispute
        await self.db.execute(
            sa_delete(DisputeARDocument).where(
                DisputeARDocument.dispute_id == dispute_id
            )
        )
        await self.db.flush()

        # Walk graph from new anchor and link all docs
        chain = await self.get_document_chain_for_doc_id(
            doc_id         = new_doc_id,
            customer_scope = customer_scope,
        )
        doc_ids = [d["doc_id"] for d in chain if d.get("doc_id")]
        if doc_ids:
            await self.link_ar_documents_to_dispute(
                dispute_id   = dispute_id,
                doc_ids      = doc_ids,
                linked_by    = user_id,
                context_note = f"Anchor manually updated to doc_id={new_doc_id} by FA user_id={user_id}",
            )

        await self.db.commit()

        logger.info(
            f"[anchor-update] dispute_id={dispute_id}: replaced anchor with "
            f"doc_id={new_doc_id}, linked {len(doc_ids)} doc(s)"
        )

        return await self.get_ar_documents_for_dispute(dispute_id)

    async def get_ar_documents_for_dispute(self, dispute_id: int) -> list[dict]:
        """
        Option A — Live graph walk on every tab open.

        Reads the anchor doc_id from dispute_ar_documents (the earliest
        agent/FA-linked row is treated as the anchor — or the one explicitly
        marked via replace_anchor_document).  Then re-runs get_document_chain_for_doc_id
        live so the Documents tab always reflects the current state of the AR
        graph, including any documents uploaded after the dispute was created.

        Falls back to the full snapshot list when no anchor is identifiable.
        Returns [] when the dispute has no linked AR documents at all.
        """
        from src.data.models.postgres.ar_document_models import DisputeARDocument, ARDocument
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload

        rows = (await self.db.execute(
            select(DisputeARDocument)
            .where(DisputeARDocument.dispute_id == dispute_id)
            .order_by(DisputeARDocument.created_at)
        )).scalars().all()

        if not rows:
            return []

        # The anchor is the first linked row (chronologically).
        # replace_anchor_document deletes all rows and re-inserts from the new
        # anchor, so the first row is always the intended anchor doc.
        anchor_doc_id = rows[0].doc_id

        # Load the anchor to get its customer_scope for the graph walk
        anchor_doc = (await self.db.execute(
            select(ARDocument)
            .options(selectinload(ARDocument.keys))
            .where(ARDocument.doc_id == anchor_doc_id)
        )).scalar_one_or_none()

        if not anchor_doc:
            # Anchor deleted — fall back to snapshot of all stored doc_ids
            return await self._get_ar_docs_snapshot(rows)

        # Live graph walk from anchor — always up to date
        chain = await self.get_document_chain_for_doc_id(
            doc_id         = anchor_doc_id,
            customer_scope = anchor_doc.customer_scope,
        )

        # get_document_chain_for_doc_id already includes the anchor at position 0
        return chain

    async def _get_ar_docs_snapshot(self, rows) -> list[dict]:
        """
        Fallback: load exactly the doc_ids stored in dispute_ar_documents.
        Used only when the anchor document has been deleted.
        """
        from src.data.models.postgres.ar_document_models import ARDocument
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload

        notes_map = {r.doc_id: r.context_note for r in rows}
        doc_ids   = [r.doc_id for r in rows]

        docs = (await self.db.execute(
            select(ARDocument)
            .options(selectinload(ARDocument.keys))
            .where(ARDocument.doc_id.in_(doc_ids))
        )).scalars().all()

        result = []
        for doc in docs:
            fmt = {
                **self._fmt_doc(doc),
                "shared_keys":       [],
                "all_keys":          [self._fmt_key(k) for k in (doc.keys or [])],
                "context_note":      notes_map.get(doc.doc_id),
                "related_documents": [],
            }
            result.append(fmt)
        return result

