# invoice_schemas.py — Invoice and Payment Pydantic schemas
from pydantic import BaseModel
from datetime import datetime
from typing import Optional, List, Any


class InvoiceResponse(BaseModel):
    invoice_id: int
    invoice_number: str
    invoice_url: str
    invoice_details: Any
    updated_at: datetime
    model_config = {"from_attributes": True}


class InvoiceListResponse(BaseModel):
    total: int
    items: List[InvoiceResponse]


class InvoiceUploadResponse(BaseModel):
    invoice_id: int
    invoice_number: str
    extracted_data: Any
    message: str


class PaymentDetailResponse(BaseModel):
    payment_detail_id: int
    customer_id: str
    invoice_number: str
    payment_url: str
    payment_details: Optional[Any] = None
    model_config = {"from_attributes": True}


class PaymentDetailListResponse(BaseModel):
    invoice_number: str
    total: int
    items: List[PaymentDetailResponse]


class CustomerPaymentListResponse(BaseModel):
    customer_id: str
    total: int
    items: List[PaymentDetailResponse]
