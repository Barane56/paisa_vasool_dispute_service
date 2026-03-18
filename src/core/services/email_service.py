import logging
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession

from src.data.repositories.repositories import EmailRepository
from src.data.models.postgres.models import EmailInbox, EmailAttachment
from src.utils.pdf_extractor import extract_text_from_bytes
from src.core.exceptions import (
    FileTooLargeError, UnsupportedFileTypeError, EmailNotFoundError, EmailProcessingError
)
from src.config.settings import settings
from src.schemas.schemas import EmailIngestResponse

logger = logging.getLogger(__name__)


class EmailService:
    def __init__(self, db: AsyncSession):
        self.repo = EmailRepository(db)
        self.db = db

    async def ingest_email_pdf(
        self,
        file_bytes: bytes,
        file_name: str,
        sender_email: str,
        subject: str,
    ) -> EmailIngestResponse:
        # Validate file size
        max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
        if len(file_bytes) > max_bytes:
            raise FileTooLargeError(settings.MAX_UPLOAD_SIZE_MB)

        # Validate type
        file_ext = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
        if file_ext not in settings.ALLOWED_FILE_TYPES:
            raise UnsupportedFileTypeError(file_ext, settings.ALLOWED_FILE_TYPES)

        # Extract text from PDF
        extracted_text = extract_text_from_bytes(file_bytes, file_ext)
        if not extracted_text.strip():
            extracted_text = f"[No extractable text found in {file_name}]"

        # Create email_inbox record
        email = EmailInbox(
            sender_email=sender_email,
            subject=subject,
            body_text=extracted_text[:5000],
            received_at=datetime.now(timezone.utc),
            has_attachment=True,
            processing_status="RECEIVED",
        )
        self.db.add(email)
        await self.db.flush()

        # Create attachment record
        attachment = EmailAttachment(
            email_id=email.email_id,
            file_name=file_name,
            file_type=file_ext,
            extracted_text=extracted_text,
        )
        self.db.add(attachment)
        await self.db.flush()
        await self.db.commit()

        # Enqueue background processing
        try:
            from src.control.tasks import process_email_task
            task = process_email_task.delay(
                email_id=email.email_id,
                sender_email=sender_email,
                subject=subject,
                body_text=extracted_text,
                attachment_texts=[extracted_text],
            )
            task_id = task.id
        except Exception as e:
            logger.error(f"Failed to enqueue email processing task: {e}")
            raise EmailProcessingError(f"Email saved but could not be queued for processing: {e}")

        logger.info(f"Email id={email.email_id} enqueued as task {task_id}")

        return EmailIngestResponse(
            email_id=email.email_id,
            processing_status="RECEIVED",
            task_id=task_id,
        )

    async def get_email(self, email_id: int):
        email = await self.repo.get_by_id(email_id)
        if not email:
            raise EmailNotFoundError(email_id)
        return email

    async def list_emails(self, status: str = None, limit: int = 20, offset: int = 0):
        if status:
            items = await self.repo.get_by_status(status, limit, offset)
        else:
            items = await self.repo.get_all(limit, offset)
        total = await self.repo.count()
        return items, total
