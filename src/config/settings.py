from pydantic_settings import BaseSettings
from functools import lru_cache
from typing import List


class Settings(BaseSettings):
    # App
    APP_NAME: str = "Paisa Vasool - Dispute Service"
    VERSION: str = "1.0.0"
    DEBUG: bool = False
    HOST: str = "0.0.0.0"
    PORT: int = 8002
    LOG_LEVEL: str = "INFO"

    # Security – Dispute service shares the same JWT secret as auth service
    # so it can validate tokens issued by auth service without calling it
    SECRET_KEY: str = "change-me-in-production"
    ALGORITHM: str = "HS256"

    # Database (shared DB, same as auth service)
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/paisa_vasool"
    DATABASE_POOL_SIZE: int = 10
    DATABASE_MAX_OVERFLOW: int = 20

    # Redis & Celery
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"

    # Groq API (replaces OpenAI)
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "llama-3.3-70b-versatile"          # chat/classify/respond
    GROQ_INVOICE_MODEL: str = "llama-3.3-70b-versatile"   # invoice data extraction

    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-2.0-flash"
    GEMINI_INVOICE_MODEL: str = "gemini-2.0-flash"

    # Embeddings – Groq doesn't do embeddings; use a lightweight local model or
    # a separate provider. For now we skip real embeddings (store None).

    # CORS
    ALLOWED_ORIGINS: List[str] = ["http://localhost:3000", "http://localhost:8000"]

    # File upload
    MAX_UPLOAD_SIZE_MB: int = 10
    ALLOWED_FILE_TYPES: List[str] = ["pdf"]

    # Email attachment storage
    ATTACHMENT_STORAGE_DIR: str = "/tmp/dispute_attachments"

    # IMAP polling interval in seconds (Celery beat)
    EMAIL_POLL_INTERVAL_SECONDS: int = 60

    # Memory / summarization threshold
    EPISODE_SUMMARIZE_THRESHOLD: int = 10

    # Langfuse observability
    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_SECRET_KEY: str = ""
    LANGFUSE_BASE_URL: str = "https://cloud.langfuse.com"

    EMBEDDING_MODEL: str = "BAAI/bge-base-en-v1.5"
    EMBEDDING_DIMS: int = 768
    EPISODE_SIMILARITY_THRESHOLD: float = 0.75

    class Config:
        env_file = ".env"
        case_sensitive = True


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()