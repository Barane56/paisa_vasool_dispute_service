# common_schemas.py — shared/utility Pydantic schemas
from pydantic import BaseModel
from typing import Optional, Any


class CurrentUser(BaseModel):
    user_id: int
    name: str
    email: str
    role: str = "finance_associate"  # "admin" | "finance_associate"


class ErrorResponse(BaseModel):
    error: str
    detail: Optional[Any] = None
    status_code: int


class SuccessResponse(BaseModel):
    message: str
    data: Optional[Any] = None


class TaskResponse(BaseModel):
    task_id: str
    status: str
    message: str


class HealthResponse(BaseModel):
    status: str
    version: str
    database: str
    redis: str
