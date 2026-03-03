from typing import Optional, Any, Dict


class PaisaVasoolException(Exception):
    """Base exception for all app errors."""

    def __init__(
        self,
        message: str,
        status_code: int = 500,
        detail: Optional[Any] = None,
    ):
        self.message = message
        self.status_code = status_code
        self.detail = detail
        super().__init__(message)


class NotFoundError(PaisaVasoolException):
    def __init__(self, resource: str, identifier: Any):
        super().__init__(
            message=f"{resource} with id '{identifier}' not found.",
            status_code=404,
        )


class AlreadyExistsError(PaisaVasoolException):
    def __init__(self, resource: str, field: str, value: Any):
        super().__init__(
            message=f"{resource} with {field}='{value}' already exists.",
            status_code=409,
        )


class UnauthorizedError(PaisaVasoolException):
    def __init__(self, message: str = "Unauthorized"):
        super().__init__(message=message, status_code=401)


class ForbiddenError(PaisaVasoolException):
    def __init__(self, message: str = "Access forbidden"):
        super().__init__(message=message, status_code=403)


class ValidationError(PaisaVasoolException):
    def __init__(self, message: str, errors: Optional[Dict] = None):
        super().__init__(message=message, status_code=422, detail=errors)


class FileTooLargeError(PaisaVasoolException):
    def __init__(self, max_mb: int):
        super().__init__(
            message=f"File exceeds maximum allowed size of {max_mb}MB.",
            status_code=413,
        )


class UnsupportedFileTypeError(PaisaVasoolException):
    def __init__(self, file_type: str, allowed: list):
        super().__init__(
            message=f"File type '{file_type}' not supported. Allowed: {allowed}",
            status_code=415,
        )


class EmailProcessingError(PaisaVasoolException):
    def __init__(self, message: str):
        super().__init__(message=message, status_code=500)


class LLMError(PaisaVasoolException):
    def __init__(self, message: str):
        super().__init__(message=message, status_code=502)


class InvoiceExtractionError(PaisaVasoolException):
    def __init__(self, message: str):
        super().__init__(message=f"Invoice extraction failed: {message}", status_code=422)


class InvoiceNotFoundError(NotFoundError):
    def __init__(self, invoice_number: str):
        super().__init__(resource="Invoice", identifier=invoice_number)


class PaymentNotFoundError(NotFoundError):
    def __init__(self, identifier: Any):
        super().__init__(resource="PaymentDetail", identifier=identifier)


class DisputeNotFoundError(NotFoundError):
    def __init__(self, dispute_id: Any):
        super().__init__(resource="Dispute", identifier=dispute_id)


class DisputeTypeNotFoundError(NotFoundError):
    def __init__(self, identifier: Any):
        super().__init__(resource="DisputeType", identifier=identifier)


class EmailNotFoundError(NotFoundError):
    def __init__(self, email_id: Any):
        super().__init__(resource="Email", identifier=email_id)


class AnalysisNotFoundError(NotFoundError):
    def __init__(self, dispute_id: Any):
        super().__init__(resource="AI Analysis", identifier=f"dispute_id={dispute_id}")


class SummaryNotFoundError(NotFoundError):
    def __init__(self, dispute_id: Any):
        super().__init__(resource="Memory Summary", identifier=f"dispute_id={dispute_id}")


class QuestionNotFoundError(NotFoundError):
    def __init__(self, question_id: Any):
        super().__init__(resource="OpenQuestion", identifier=question_id)


class UserNotFoundError(NotFoundError):
    def __init__(self, user_id: Any):
        super().__init__(resource="User", identifier=user_id)


class TokenExpiredError(UnauthorizedError):
    def __init__(self):
        super().__init__(message="Token has expired")


class InvalidTokenError(UnauthorizedError):
    def __init__(self):
        super().__init__(message="Invalid token")


class DisputeStatusTransitionError(PaisaVasoolException):
    def __init__(self, current: str, requested: str):
        super().__init__(
            message=f"Cannot transition dispute from '{current}' to '{requested}'.",
            status_code=422,
        )


class TaskEnqueueError(PaisaVasoolException):
    def __init__(self, task_name: str):
        super().__init__(
            message=f"Failed to enqueue task '{task_name}'. Check Celery/Redis.",
            status_code=503,
        )
