"""Pydantic schemas that every AI response must satisfy before it is used."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

Severity = Literal["critical", "high", "medium", "low", "informational"]
RiskCategory = Literal["financial", "legal", "operational", "data_protection", "intellectual_property", "ambiguity", "unusual"]
ClauseKey = Literal["confidentiality", "intellectual_property", "liability", "indemnification", "termination",
                    "dispute_resolution", "data_protection", "non_compete", "exclusivity", "warranties"]


def _clip(v: str | None, n: int) -> str | None:
    return v.strip()[:n] if isinstance(v, str) and v.strip() else None


class AIParty(BaseModel):
    name: str = Field(max_length=300)
    role: str | None = None
    quote: str | None = None


class AIValue(BaseModel):
    """A value plus the verbatim quote that supports it."""

    value: str | None = None
    quote: str | None = None

    @field_validator("value", "quote")
    @classmethod
    def _c(cls, v: str | None) -> str | None:
        return _clip(v, 1500)


class AIRisk(BaseModel):
    title: str = Field(max_length=200)
    description: str = Field(max_length=2000)
    category: RiskCategory
    severity: Severity
    quote: str = Field(max_length=2000)
    section_ref: str | None = Field(default=None, max_length=120)
    explanation: str = Field(default="", max_length=2000)
    suggested_actions: list[str] = Field(default_factory=list, max_length=6)


class AIObligation(BaseModel):
    party: str | None = Field(default=None, max_length=200)
    description: str = Field(max_length=1000)
    obligation_type: str = Field(default="general", max_length=40)
    due_description: str | None = Field(default=None, max_length=300)
    trigger_condition: str | None = Field(default=None, max_length=500)
    consequence: str | None = Field(default=None, max_length=500)
    quote: str = Field(max_length=2000)


class AIClause(BaseModel):
    present: bool
    summary: str | None = Field(default=None, max_length=1200)
    quote: str | None = Field(default=None, max_length=2000)


class AIFee(BaseModel):
    description: str = Field(max_length=500)
    amount: str | None = Field(default=None, max_length=100)
    quote: str = Field(max_length=1500)


class AIAnalysis(BaseModel):
    summary: str = Field(max_length=4000)
    contract_title: AIValue = AIValue()
    contract_type: AIValue = AIValue()
    parties: list[AIParty] = Field(default_factory=list, max_length=10)
    effective_date: AIValue = AIValue()
    expiration_date: AIValue = AIValue()
    renewal_terms: AIValue = AIValue()
    auto_renewal: bool | None = None
    notice_period_days: int | None = Field(default=None, ge=0, le=3650)
    governing_law: AIValue = AIValue()
    jurisdiction: AIValue = AIValue()
    payment_terms: AIValue = AIValue()
    late_payment_penalties: AIValue = AIValue()
    price_adjustment: AIValue = AIValue()
    fees: list[AIFee] = Field(default_factory=list, max_length=30)
    clauses: dict[ClauseKey, AIClause] = Field(default_factory=dict)
    obligations: list[AIObligation] = Field(default_factory=list, max_length=60)
    risks: list[AIRisk] = Field(default_factory=list, max_length=40)


class AICitation(BaseModel):
    chunk_id: str = Field(max_length=20)
    quote: str = Field(max_length=2000)


class AIAnswer(BaseModel):
    found: bool
    answer: str = Field(max_length=6000)
    interpretation: str | None = Field(default=None, max_length=3000)
    citations: list[AICitation] = Field(default_factory=list, max_length=8)
