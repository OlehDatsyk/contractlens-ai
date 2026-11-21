"""API request/response schemas."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models import Role


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


def _strip(v: str) -> str:
    v = " ".join(v.split())
    if not v:
        raise ValueError("Must not be blank")
    return v


CleanStr = Annotated[str, AfterValidator(_strip)]


# --- Auth -----------------------------------------------------------------------------------------------------

class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)
    full_name: CleanStr = Field(min_length=1, max_length=120)
    workspace_name: str | None = Field(default=None, max_length=120)



class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class UserOut(ORM):
    id: uuid.UUID
    email: str
    full_name: str
    created_at: datetime
    email_notifications: bool


class ProfileUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=120)
    email_notifications: bool | None = None


class PasswordChange(BaseModel):
    current_password: str = Field(max_length=256)
    new_password: str = Field(max_length=256)


class ForgotPasswordIn(BaseModel):
    email: EmailStr


class ResetPasswordIn(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    new_password: str = Field(max_length=256)


# --- Workspaces -----------------------------------------------------------------------------------------------

class WorkspaceIn(BaseModel):
    name: CleanStr = Field(min_length=1, max_length=120)


class WorkspaceOut(ORM):
    id: uuid.UUID
    name: str
    role: Role
    created_at: datetime
    member_count: int = 0


class AISettingsOut(BaseModel):
    available: bool
    provider: str | None
    model: str | None
    source: str | None
    reason: str | None
    ai_enabled: bool
    workspace_provider: str | None
    workspace_model: str | None
    workspace_key_set: bool
    workspace_key_hint: str | None
    server_provider: str | None


class WorkspaceSettingsOut(BaseModel):
    id: uuid.UUID
    name: str
    retention_days: int | None
    ai: AISettingsOut


class WorkspaceSettingsUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    retention_days: int | None = Field(default=None, ge=1, le=3650)
    clear_retention: bool = False
    ai_enabled: bool | None = None
    ai_provider: Literal["openai", "anthropic", "gemini", "server"] | None = None
    ai_model: str | None = Field(default=None, max_length=100, pattern=r"^[A-Za-z0-9._:\-/]*$")
    ai_api_key: str | None = Field(default=None, max_length=300)
    clear_ai_key: bool = False


class MemberOut(BaseModel):
    user_id: uuid.UUID
    email: str
    full_name: str
    role: Role
    joined_at: datetime


class MemberUpdate(BaseModel):
    role: Role


class InvitationIn(BaseModel):
    email: EmailStr
    role: Role = Role.viewer


class InvitationOut(ORM):
    id: uuid.UUID
    workspace_id: uuid.UUID
    email: str
    role: Role
    expires_at: datetime
    accepted_at: datetime | None
    created_at: datetime
    workspace_name: str | None = None


class FolderIn(BaseModel):
    name: CleanStr = Field(min_length=1, max_length=80)


class FolderOut(ORM):
    id: uuid.UUID
    name: str
    contract_count: int = 0


# --- Contracts ------------------------------------------------------------------------------------------------

class DocumentOut(ORM):
    id: uuid.UUID
    original_filename: str
    file_type: str
    size_bytes: int
    page_count: int | None
    char_count: int | None
    has_page_numbers: bool
    extraction_method: str | None
    extraction_warnings: list[str]
    created_at: datetime


class ContractOut(ORM):
    id: uuid.UUID
    title: str
    status: str
    contract_type: str | None
    risk_level: str | None
    risk_score: int | None
    error_message: str | None
    analyzed_at: datetime | None
    expiration_date: date | None
    folder_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    document: DocumentOut | None = None


class ContractPage(BaseModel):
    items: list[ContractOut]
    total: int
    page: int
    page_size: int


class ContractUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    folder_id: uuid.UUID | None = None
    clear_folder: bool = False


class ClauseOut(ORM):
    id: uuid.UUID
    position: int
    category: str
    section_ref: str | None
    heading: str | None
    text: str
    page_number: int | None


class FindingOut(ORM):
    id: uuid.UUID
    title: str
    description: str
    category: str
    severity: str
    page_number: int | None
    section_ref: str | None
    excerpt: str | None
    explanation: str
    suggested_actions: list[str]
    source: str
    rule_id: str | None
    evidence_verified: bool


class ObligationOut(ORM):
    id: uuid.UUID
    party: str | None
    description: str
    obligation_type: str
    due_description: str | None
    trigger_condition: str | None
    consequence: str | None
    page_number: int | None
    section_ref: str | None
    excerpt: str | None
    source: str
    evidence_verified: bool


class AnalysisOut(ORM):
    id: uuid.UUID
    engine: str
    ai_used: bool
    ai_error: str | None
    input_truncated: bool
    summary: str | None
    contract_title: str | None
    contract_type: str | None
    parties: list[dict[str, Any]]
    effective_date: date | None
    expiration_date: date | None
    term_description: str | None
    renewal_terms: str | None
    auto_renewal: bool | None
    notice_period_days: int | None
    governing_law: str | None
    jurisdiction: str | None
    payment_terms: str | None
    payment_deadlines: list[dict[str, Any]]
    late_payment_penalties: str | None
    fees: list[dict[str, Any]]
    financial_obligations: list[dict[str, Any]]
    price_adjustment: str | None
    clause_overview: dict[str, Any]
    field_evidence: dict[str, Any]
    risk_score: int
    risk_level: str
    score_breakdown: dict[str, Any]
    created_at: datetime


class DownloadLinkOut(BaseModel):
    url: str
    expires_in: int


# --- Assistant ------------------------------------------------------------------------------------------------

class ConversationIn(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class ConversationOut(ORM):
    id: uuid.UUID
    contract_id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime


class MessageIn(BaseModel):
    content: str = Field(min_length=2, max_length=1000)


class MessageOut(ORM):
    id: uuid.UUID
    role: str
    content: str
    interpretation: str | None
    citations: list[dict[str, Any]]
    answer_found: bool | None
    mode: str | None
    created_at: datetime


# --- Deadlines ------------------------------------------------------------------------------------------------

DeadlineType = Literal["expiration", "renewal", "payment", "notice", "obligation", "custom"]


class DeadlineIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    deadline_type: DeadlineType = "custom"
    due_date: date
    description: str | None = Field(default=None, max_length=2000)
    contract_id: uuid.UUID | None = None
    reminder_days: list[int] = Field(default_factory=lambda: [30, 7, 1], max_length=6)

    @field_validator("reminder_days")
    @classmethod
    def _rd(cls, v: list[int]) -> list[int]:
        if any(d < 0 or d > 365 for d in v):
            raise ValueError("Reminder offsets must be between 0 and 365 days")
        return sorted(set(v), reverse=True)


class DeadlineUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    deadline_type: DeadlineType | None = None
    due_date: date | None = None
    description: str | None = Field(default=None, max_length=2000)
    status: Literal["open", "done", "dismissed"] | None = None
    reminder_days: list[int] | None = Field(default=None, max_length=6)

    @field_validator("reminder_days")
    @classmethod
    def _rd(cls, v: list[int] | None) -> list[int] | None:
        if v is not None and any(d < 0 or d > 365 for d in v):
            raise ValueError("Reminder offsets must be between 0 and 365 days")
        return sorted(set(v), reverse=True) if v is not None else None


class DeadlineOut(ORM):
    id: uuid.UUID
    title: str
    deadline_type: str
    due_date: date
    description: str | None
    status: str
    reminder_days: list[int]
    source: str
    contract_id: uuid.UUID | None
    contract_title: str | None = None
    created_at: datetime


# --- Reports / notifications / audit --------------------------------------------------------------------------

class ReportIn(BaseModel):
    contract_id: uuid.UUID
    format: Literal["pdf", "csv", "json"] = "pdf"


class ReportOut(ORM):
    id: uuid.UUID
    contract_id: uuid.UUID
    title: str
    format: str
    size_bytes: int
    created_at: datetime
    contract_title: str | None = None


class NotificationOut(ORM):
    id: uuid.UUID
    workspace_id: uuid.UUID | None
    kind: str
    title: str
    body: str
    link: str | None
    read_at: datetime | None
    created_at: datetime


class AuditOut(ORM):
    id: uuid.UUID
    action: str
    user_email: str | None = None
    target_type: str | None
    target_id: str | None
    ip_address: str | None
    details: dict[str, Any]
    created_at: datetime
