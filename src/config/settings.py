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
    SECRET_KEY: str = ""
    ALGORITHM: str = "HS256"

    # Database (shared DB, same as auth service)
    DATABASE_URL: str = ""
    DATABASE_POOL_SIZE: int = 10
    DATABASE_MAX_OVERFLOW: int = 20

    # Redis & Celery
    REDIS_URL: str = ""
    CELERY_BROKER_URL: str = ""
    CELERY_RESULT_BACKEND: str = ""

    # Groq API (replaces OpenAI)
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "llama-3.3-70b-versatile"          # heavy tasks: response generation, draft email
    GROQ_FAST_MODEL: str = "llama-3.1-8b-instant"         # light tasks: extract, summarize
    GROQ_REASONING_MODEL: str = "qwen/qwen3-32b"            # reasoning tasks: classify, detect context shift
    GROQ_INVOICE_MODEL: str = "llama-3.1-8b-instant"      # invoice extraction (8b handles JSON fine)

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

    # Email attachment storage (local fallback — not used when GCS is enabled)
    ATTACHMENT_STORAGE_DIR: str = "/tmp/dispute_attachments"

    # ── Google Cloud Storage ──────────────────────────────────────────────────
    GCS_ENABLED: bool = False   # set True in production .env when GCS is configured
    GCS_PROJECT_ID: str = ""
    GCS_BUCKET_NAME: str = ""
    GCS_BUCKET_PREFIX: str = ""
    GCS_TARGET_SERVICE_ACCOUNT: str = ""

    # ── AI Agent outbound email credentials ──────────────────────────────────
    # These are used exclusively by the AI auto-responder when sending replies
    # on behalf of the system.  Human FA replies still use the mailbox credentials.
    
    AGENT_EMAIL: str = ""
    AGENT_EMAIL_PASSWORD: str = ""   # plain-text; encoded at runtime
    AGENT_SMTP_HOST: str = ""
    AGENT_SMTP_PORT: int = 587
    AGENT_SMTP_USE_TLS: bool = True                # True = STARTTLS on port 587

    # IMAP polling interval in seconds (Celery beat)
    EMAIL_POLL_INTERVAL_SECONDS: int = 20

    # Memory / summarization threshold
    EPISODE_SUMMARIZE_THRESHOLD: int = 10

    # Langfuse observability
    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_SECRET_KEY: str = ""
    LANGFUSE_BASE_URL: str = "https://cloud.langfuse.com"

    EMBEDDING_MODEL: str = "BAAI/bge-base-en-v1.5"
    EMBEDDING_DIMS: int = 768
    EPISODE_SIMILARITY_THRESHOLD: float = 0.75

    FASTEMBED_CACHE_PATH:str ="/app/.fastembed_cache"

    class Config:
        env_file = ".env"
        case_sensitive = True


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
