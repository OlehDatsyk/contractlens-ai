"""Application configuration loaded from environment variables (and an optional backend/.env file)."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
# Development-only default; production startup rejects it (see _production_checks).
INSECURE_DEV_SECRET = "dev-only-insecure-secret-change-me-0123456789abcdef"  # nosec B105


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR / ".env"), env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    # --- General -------------------------------------------------------------------------------------------------
    environment: Literal["development", "production", "test"] = "development"
    app_name: str = "ContractLens AI"
    log_level: str = "INFO"
    frontend_url: str = "http://localhost:5173"
    backend_url: str = "http://localhost:8000"
    cors_origins: str = ""  # comma separated; FRONTEND_URL is always allowed
    trust_proxy_headers: bool = False
    docs_enabled: bool = True
    registration_enabled: bool = True
    serve_frontend_dir: str = ""  # path to the built frontend (dist) when the API also serves the SPA

    # --- Security ------------------------------------------------------------------------------------------------
    jwt_secret_key: str = INSECURE_DEV_SECRET
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    access_token_minutes: int = Field(default=15, ge=1, le=120)
    refresh_token_days: int = Field(default=14, ge=1, le=90)
    encryption_key: str = ""  # Fernet key used to encrypt stored credentials (workspace AI keys)
    password_reset_minutes: int = Field(default=30, ge=5, le=1440)
    download_link_seconds: int = Field(default=120, ge=10, le=3600)
    rate_limit_enabled: bool = True
    max_failed_logins: int = Field(default=8, ge=3, le=100)
    lockout_minutes: int = Field(default=15, ge=1, le=1440)

    # --- Database ------------------------------------------------------------------------------------------------
    database_url: str = "postgresql+psycopg://contractlens:contractlens@127.0.0.1:5437/contractlens"
    # Optional PostgreSQL schema for all tables (lets ContractLens share a database instance safely with other apps).
    database_schema: str = Field(default="", pattern=r"^([a-z_][a-z0-9_]{0,62})?$")
    db_pool_size: int = 5
    db_max_overflow: int = 5

    # --- Uploads / processing ------------------------------------------------------------------------------------
    max_upload_mb: int = Field(default=20, ge=1, le=200)
    max_pdf_pages: int = Field(default=300, ge=1, le=5000)
    max_text_chars: int = Field(default=1_500_000, ge=10_000)
    max_docx_uncompressed_mb: int = Field(default=100, ge=1, le=2000)
    processing_timeout_seconds: int = Field(default=180, ge=10, le=3600)
    ocr_enabled: bool = True
    ocr_max_pages: int = Field(default=30, ge=1, le=500)

    # --- Storage -------------------------------------------------------------------------------------------------
    storage_backend: Literal["local", "s3", "database"] = "local"
    local_storage_path: str = str(BACKEND_DIR / "storage")
    local_storage_persistent: bool = False  # set true when LOCAL_STORAGE_PATH is on a persistent disk
    object_storage_endpoint: str = ""
    object_storage_bucket: str = ""
    object_storage_access_key: str = ""
    object_storage_secret_key: str = ""
    object_storage_region: str = "auto"

    # --- AI ------------------------------------------------------------------------------------------------------
    ai_provider: Literal["none", "openai", "anthropic", "gemini"] = "none"
    openai_api_key: str = ""
    openai_model: str = "gpt-4.1-mini"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5-5"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    ai_timeout_seconds: int = Field(default=60, ge=5, le=600)
    ai_max_input_chars: int = Field(default=60_000, ge=2_000, le=600_000)
    ai_max_output_tokens: int = Field(default=4_000, ge=256, le=32_000)
    ai_max_retries: int = Field(default=1, ge=0, le=3)

    # --- Email ---------------------------------------------------------------------------------------------------
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_starttls: bool = True

    # --- Background work -----------------------------------------------------------------------------------------
    run_worker_in_api: bool = True
    worker_poll_seconds: float = Field(default=2.0, ge=0.2, le=60)
    job_lock_timeout_seconds: int = Field(default=900, ge=60)
    reminder_check_minutes: int = Field(default=30, ge=1, le=1440)
    retention_check_minutes: int = Field(default=360, ge=5)

    @field_validator("database_url")
    @classmethod
    def _normalise_db_url(cls, v: str) -> str:
        # Render/Heroku style URLs -> SQLAlchemy + psycopg 3 driver
        if v.startswith("postgres://"):
            v = "postgresql+psycopg://" + v[len("postgres://") :]
        elif v.startswith("postgresql://"):
            v = "postgresql+psycopg://" + v[len("postgresql://") :]
        return v

    @field_validator("local_storage_path")
    @classmethod
    def _absolute_storage(cls, v: str) -> str:
        # Relative paths are resolved against backend/ so the location does not depend on the working directory.
        p = Path(v)
        return str(p if p.is_absolute() else (BACKEND_DIR / p).resolve())

    @model_validator(mode="after")
    def _production_checks(self) -> Settings:
        # On Render, default the public URLs to the service's own HTTPS URL (single-origin deployment).
        render_url = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
        if render_url:
            if "frontend_url" not in self.model_fields_set:
                self.frontend_url = render_url
            if "backend_url" not in self.model_fields_set:
                self.backend_url = render_url
        if self.environment != "production":
            return self
        problems: list[str] = []
        if self.jwt_secret_key == INSECURE_DEV_SECRET or len(self.jwt_secret_key) < 32:
            problems.append("JWT_SECRET_KEY must be set to a random value of at least 32 characters")
        if not self.encryption_key:
            problems.append("ENCRYPTION_KEY must be set (a Fernet key) in production")
        if self.storage_backend == "local" and not self.local_storage_persistent:
            problems.append(
                "STORAGE_BACKEND=local uses the container filesystem, which is ephemeral on most hosts. "
                "Use STORAGE_BACKEND=s3 or database, or set LOCAL_STORAGE_PERSISTENT=true for a mounted disk"
            )
        if self.storage_backend == "s3" and not (self.object_storage_bucket and self.object_storage_access_key):
            problems.append("STORAGE_BACKEND=s3 requires OBJECT_STORAGE_BUCKET and access credentials")
        if self.database_url.startswith("sqlite"):
            problems.append("SQLite is not supported in production; set DATABASE_URL to PostgreSQL")
        if problems:
            raise ValueError("Invalid production configuration: " + "; ".join(problems))
        return self

    # --- Derived helpers -----------------------------------------------------------------------------------------
    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def allowed_origins(self) -> list[str]:
        origins = {o.strip().rstrip("/") for o in self.cors_origins.split(",") if o.strip()}
        if self.frontend_url:
            origins.add(self.frontend_url.rstrip("/"))
        return sorted(origins)

    @property
    def email_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_from)

    def provider_key(self, provider: str) -> str:
        return {
            "openai": self.openai_api_key,
            "anthropic": self.anthropic_api_key,
            "gemini": self.gemini_api_key,
        }.get(provider, "")

    def provider_model(self, provider: str) -> str:
        return {
            "openai": self.openai_model,
            "anthropic": self.anthropic_model,
            "gemini": self.gemini_model,
        }.get(provider, "")


@lru_cache
def get_settings() -> Settings:
    return Settings()
