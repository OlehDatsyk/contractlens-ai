"""Contract analysis pipeline: extraction -> clauses -> deterministic rules -> optional AI -> verification -> scoring.

The whole result for a contract is replaced atomically in one transaction, so a failure never leaves a
half-written analysis behind. AI output is never trusted blindly: every quote is located in the document, dates
must be present in the quoted text, and unverifiable AI findings are labelled as such and excluded from scoring.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import (
    AnalysisResult,
    Contract,
    ContractStatus,
    Deadline,
    Document,
    DocumentChunk,
    ExtractedClause,
    Obligation,
    RiskFinding,
    RiskLevel,
    Workspace,
    WorkspaceMembership,
)
from app.services import notifications
from app.services.ai.config import resolve_provider
from app.services.ai.prompts import ANALYSIS_SYSTEM, frame_document
from app.services.ai.providers import AIError, AIProvider, call_structured
from app.services.ai.schemas import AIAnalysis, AIValue
from app.services.extraction import (
    Clause,
    ExtractedDocument,
    ExtractionError,
    build_chunks,
    extract,
    extract_isolated,
    section_label,
    segment_clauses,
)
from app.services.grounding import EvidenceIndex, norm
from app.services.rules import (
    Evidence,
    ExtractedFields,
    FieldResult,
    Finding,
    ObligationItem,
    classify_clause,
    detect_risks,
    extract_fields,
    extract_obligations,
    find_dates,
)
from app.services.scoring import compute_score
from app.services.storage import StorageError, get_storage

log = logging.getLogger(__name__)


class AnalysisFailed(RuntimeError):
    """User-presentable analysis failure."""


# --- AI step ------------------------------------------------------------------------------------------------

def _ai_input(clauses: list[Clause], budget: int) -> tuple[str, bool]:
    blocks: list[tuple[str, str]] = []
    used = 0
    truncated = False
    # Prioritise categorised clauses when the document exceeds the budget, then restore document order.
    order = sorted(clauses, key=lambda c: (c.category == "general", c.position))
    chosen: list[Clause] = []
    for c in order:
        if used + len(c.text) > budget:
            truncated = True
            continue
        chosen.append(c)
        used += len(c.text)
    for c in sorted(chosen, key=lambda c: c.position):
        label = " ".join(x for x in [f"§{c.section_ref}" if c.section_ref else None,
                                     c.heading or c.parent_heading, f"p.{c.page_number}" if c.page_number else None] if x)
        blocks.append((label or f"part {c.position + 1}", c.text))
    return frame_document(blocks), truncated


def run_ai(provider: AIProvider, clauses: list[Clause]) -> tuple[AIAnalysis, bool]:
    s = get_settings()
    doc, truncated = _ai_input(clauses, s.ai_max_input_chars)
    note = "\nNote: the document was shortened to fit the analysis budget; some general clauses are omitted." if truncated else ""
    user = f"Analyse the following contract.{note}\n\n{doc}"
    return call_structured(provider, ANALYSIS_SYSTEM, user, AIAnalysis), truncated


def _parse_iso(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _ai_text(field: AIValue, idx: EvidenceIndex) -> FieldResult | None:
    if not field.value:
        return None
    loc = idx.locate(field.quote)
    if loc is None:
        return None
    return FieldResult(field.value[:600], Evidence((field.quote or "")[:600], loc.page, loc.section), source="ai")


def _ai_date(field: AIValue, idx: EvidenceIndex) -> FieldResult | None:
    d = _parse_iso(field.value)
    if d is None or not field.quote:
        return None
    loc = idx.locate(field.quote)
    if loc is None:
        return None
    if d not in {fd.value for fd in find_dates(field.quote)}:
        return None  # the date is not literally in the quote: treat as unverified
    return FieldResult(d, Evidence(field.quote[:600], loc.page, loc.section), source="ai")


def merge_ai(fields: ExtractedFields, ai: AIAnalysis, idx: EvidenceIndex) -> dict[str, Any]:
    """Merge verified AI values into rule-extracted fields. Returns notes on conflicts/unverified values."""
    notes: dict[str, Any] = {}
    # Text fields: a verified AI value is usually more readable than the raw rule sentence.
    for name in ("contract_title", "governing_law", "jurisdiction", "payment_terms", "late_payment_penalties",
                 "price_adjustment", "renewal_terms", "contract_type"):
        ai_field: AIValue = getattr(ai, name)
        verified = _ai_text(ai_field, idx)
        current: FieldResult | None = getattr(fields, name)
        if verified:
            setattr(fields, name, verified)
        elif ai_field.value and current is None:
            if name == "contract_type":
                fields.contract_type = FieldResult(ai_field.value[:80], None, "AI classification (no verbatim quote)", "ai")
            else:
                notes[name] = {"ai_value": ai_field.value[:300], "status": "unverified - quote not found in document"}
    # Dates: rules win; AI only fills gaps when the date is literally present in a verified quote.
    for name in ("effective_date", "expiration_date"):
        ai_field = getattr(ai, name)
        verified = _ai_date(ai_field, idx)
        current = getattr(fields, name)
        if current is None and verified:
            setattr(fields, name, verified)
        elif current is not None and verified and verified.value != current.value:
            notes[name] = {"ai_value": verified.value.isoformat(), "status": "conflict - rule value kept"}
        elif ai_field.value and not verified:
            notes[name] = {"ai_value": ai_field.value[:20], "status": "unverified - not used"}
    if fields.parties is None and ai.parties:
        located = [p for p in ai.parties if idx.contains(p.name)]
        if located:
            first = idx.locate(located[0].quote) if located[0].quote else None
            fields.parties = FieldResult([{"name": p.name[:150], "role": p.role} for p in located[:6]],
                                         Evidence((located[0].quote or located[0].name)[:600], first.page if first else None,
                                                  first.section if first else None), source="ai")
    if fields.auto_renewal is None and ai.auto_renewal is not None:
        fields.auto_renewal = FieldResult(ai.auto_renewal, None, "AI assessment without a verified quote", "ai")
    if fields.notice_period_days is None and ai.notice_period_days is not None:
        if idx.contains(str(ai.notice_period_days)):
            fields.notice_period_days = FieldResult(ai.notice_period_days, None, "AI value; number appears in the document", "ai")
        else:
            notes["notice_period_days"] = {"ai_value": ai.notice_period_days, "status": "unverified - not used"}
    for key, clause in ai.clauses.items():
        entry = fields.clause_overview.setdefault(key, {"present": False, "count": 0, "source": "rule"})
        loc = idx.locate(clause.quote) if clause.quote else None
        if clause.summary and (loc or entry.get("present")):
            entry["summary"] = clause.summary
            entry["summary_source"] = "ai"
        if clause.present and not entry.get("present") and loc:
            entry.update({"present": True, "count": 1, "source": "ai", "excerpt": (clause.quote or "")[:400],
                          "page": loc.page, "section": loc.section})
    for fee in ai.fees:
        loc = idx.locate(fee.quote)
        if loc and not any(norm(fee.quote)[:40] in norm(f["description"]) for f in fields.fees):
            fields.fees.append({"amount": fee.amount, "description": fee.description[:300], "quote": fee.quote[:600],
                                "page": loc.page, "section": loc.section, "source": "ai"})
    return notes


def ai_findings(ai: AIAnalysis, idx: EvidenceIndex, rule_findings: list[Finding]) -> list[Finding]:
    out: list[Finding] = []
    rule_quotes = [norm(f.evidence.quote) for f in rule_findings if f.evidence]
    for r in ai.risks[:15]:
        loc = idx.locate(r.quote)
        if loc and any(norm(r.quote)[:60] in q or q[:60] in norm(r.quote) for q in rule_quotes):
            continue  # same clause already flagged by a deterministic rule
        out.append(Finding(
            title=r.title, description=r.description, category=r.category, severity=r.severity,
            explanation=(r.explanation or "") + ("" if loc else " [The quoted text could not be found in the document; "
                                                  "treat this AI finding with caution. It is excluded from the score.]"),
            suggested_actions=[a[:300] for a in r.suggested_actions[:6]],
            evidence=Evidence(r.quote[:600], loc.page if loc else None, (loc.section if loc else r.section_ref)),
            rule_id=None, source="ai", verified=loc is not None,
        ))
    return out


def ai_obligations(ai: AIAnalysis, idx: EvidenceIndex) -> list[tuple[ObligationItem, bool]]:
    out = []
    for o in ai.obligations[:30]:
        loc = idx.locate(o.quote)
        out.append((ObligationItem(o.party, o.description, o.obligation_type[:40], o.due_description,
                                   o.trigger_condition, o.consequence,
                                   Evidence(o.quote[:600], loc.page if loc else None, loc.section if loc else None)),
                    loc is not None))
    return out


# --- Persistence --------------------------------------------------------------------------------------------

def _clear_previous(db: Session, contract_id: uuid.UUID) -> None:
    for model in (ExtractedClause, DocumentChunk, RiskFinding, Obligation, AnalysisResult):
        db.execute(delete(model).where(model.contract_id == contract_id))
    db.execute(delete(Deadline).where(Deadline.contract_id == contract_id, Deadline.source == "auto"))


def _auto_deadlines(contract: Contract, fields: ExtractedFields, obligations: list[ObligationItem], today: date) -> list[Deadline]:
    out: list[Deadline] = []

    def add(title: str, kind: str, due: date, description: str) -> None:
        if due >= today - timedelta(days=1):
            out.append(Deadline(workspace_id=contract.workspace_id, contract_id=contract.id, title=title[:200],
                                deadline_type=kind, due_date=due, description=description, source="auto",
                                reminder_days=[30, 7, 1]))

    if fields.expiration_date:
        exp = fields.expiration_date.value
        add(f"Expiration: {contract.title}", "expiration", exp, "Contract expiration date extracted from the document.")
        if fields.auto_renewal and fields.auto_renewal.value and fields.notice_period_days:
            notice = fields.notice_period_days.value
            add(f"Non-renewal notice deadline: {contract.title}", "notice", exp - timedelta(days=notice),
                f"Last day to give {notice} days' notice before automatic renewal (calculated: expiration minus notice period).")
        elif fields.auto_renewal and fields.auto_renewal.value:
            add(f"Renewal date: {contract.title}", "renewal", exp, "The contract renews automatically at the end of the term.")
    for ob in obligations:
        if ob.due_description:
            dates = find_dates(ob.due_description)
            if dates:
                add(f"Obligation due: {ob.description[:120]}", "obligation", dates[0].value, ob.description[:500])
    return out[:50]


def _level(value: str) -> RiskLevel:
    return RiskLevel(value)


def analyze_contract(db: Session, contract_id: uuid.UUID, *, isolate: bool = True, today: date | None = None) -> AnalysisResult:
    s = get_settings()
    today = today or date.today()
    contract = db.get(Contract, contract_id)
    if contract is None:
        raise AnalysisFailed("Contract not found")
    document = db.scalar(select(Document).where(Document.contract_id == contract.id,
                                                Document.workspace_id == contract.workspace_id))
    if document is None:
        raise AnalysisFailed("The contract has no document")
    workspace = db.get(Workspace, contract.workspace_id)

    try:
        data = get_storage().get(document.storage_key)
    except StorageError as exc:
        raise AnalysisFailed("The stored document could not be read") from exc
    try:
        extracted: ExtractedDocument = (extract_isolated(data, document.file_type, s.processing_timeout_seconds)
                                        if isolate else extract(data, document.file_type))
    except ExtractionError as exc:
        raise AnalysisFailed(str(exc)) from exc
    except Exception as exc:  # parser crash on malformed input
        log.warning("Extraction crashed for contract %s: %s", contract.id, type(exc).__name__)
        raise AnalysisFailed("The document could not be parsed") from exc
    finally:
        del data

    clauses = segment_clauses(extracted)
    for c in clauses:
        c.category = classify_clause(c)
    chunks = build_chunks(clauses)
    full_text = extracted.full_text

    fields = extract_fields(full_text, clauses, today)
    findings = detect_risks(full_text, clauses, fields, today)
    obligations = extract_obligations(clauses, fields.parties.value if fields.parties else None)
    ai_obligation_items: list[tuple[ObligationItem, bool]] = []

    engine = "rules"
    ai_used = False
    ai_error: str | None = None
    truncated = False
    summary: str | None = None
    notes: dict[str, Any] = {}
    provider = None
    try:
        provider = resolve_provider(workspace)
    except AIError as exc:
        ai_error = str(exc)
    if provider is not None:
        try:
            ai, truncated = run_ai(provider, clauses)
            idx = EvidenceIndex([(c.text, c.page_number, section_label(c.section_ref, c.heading or c.parent_heading)) for c in clauses])
            notes = merge_ai(fields, ai, idx)
            findings += ai_findings(ai, idx, findings)
            ai_obligation_items = ai_obligations(ai, idx)
            summary = ai.summary
            ai_used = True
            engine = f"rules+{provider.label}"
        except AIError as exc:
            ai_error = str(exc)[:500]
            log.warning("AI analysis unavailable for contract %s: %s", contract.id, ai_error)

    score, level, breakdown = compute_score(findings)
    if summary is None:
        summary = _rule_summary(fields, findings, extracted)

    evidence = {name: getattr(fields, name).evidence_dict() for name in (
        "contract_title", "contract_type", "parties", "effective_date", "expiration_date", "term_description",
        "renewal_terms", "auto_renewal", "notice_period_days", "governing_law", "jurisdiction", "payment_terms",
        "late_payment_penalties", "price_adjustment") if getattr(fields, name) is not None}
    if notes:
        evidence["_ai_notes"] = notes
    if fields.warnings:
        evidence["_warnings"] = fields.warnings

    def val(fr: FieldResult | None) -> Any:
        return fr.value if fr else None

    _clear_previous(db, contract.id)
    ws = contract.workspace_id
    for c in clauses:
        db.add(ExtractedClause(workspace_id=ws, contract_id=contract.id, position=c.position, category=c.category,
                               section_ref=c.section_ref, heading=(c.heading or c.parent_heading),
                               text=c.text, page_number=c.page_number))
    for ch in chunks:
        db.add(DocumentChunk(workspace_id=ws, contract_id=contract.id, chunk_index=ch.index, page_number=ch.page_number,
                             section_ref=ch.section_ref, text=ch.text))
    result = AnalysisResult(
        workspace_id=ws, contract_id=contract.id, engine=engine, ai_used=ai_used, ai_error=ai_error,
        input_truncated=truncated, summary=summary,
        contract_title=val(fields.contract_title), contract_type=val(fields.contract_type),
        parties=val(fields.parties) or [], effective_date=val(fields.effective_date),
        expiration_date=val(fields.expiration_date), term_description=val(fields.term_description),
        renewal_terms=val(fields.renewal_terms), auto_renewal=val(fields.auto_renewal),
        notice_period_days=val(fields.notice_period_days), governing_law=val(fields.governing_law),
        jurisdiction=val(fields.jurisdiction), payment_terms=val(fields.payment_terms),
        payment_deadlines=fields.payment_deadlines, late_payment_penalties=val(fields.late_payment_penalties),
        fees=fields.fees, financial_obligations=fields.financial_obligations, price_adjustment=val(fields.price_adjustment),
        clause_overview=fields.clause_overview, field_evidence=evidence,
        risk_score=score, risk_level=_level(level), score_breakdown=breakdown,
    )
    db.add(result)
    for f in findings:
        db.add(RiskFinding(
            workspace_id=ws, contract_id=contract.id, title=f.title[:200], description=f.description,
            category=f.category, severity=_level(f.severity), page_number=f.evidence.page if f.evidence else None,
            section_ref=(f.evidence.section if f.evidence else None), excerpt=f.evidence.quote if f.evidence else None,
            explanation=f.explanation, suggested_actions=f.suggested_actions, source=f.source, rule_id=f.rule_id,
            evidence_verified=f.verified,
        ))
    for ob in obligations:
        db.add(_obligation_row(ws, contract.id, ob, "rule", True))
    for ob, verified in ai_obligation_items:
        db.add(_obligation_row(ws, contract.id, ob, "ai", verified))
    for d in _auto_deadlines(contract, fields, obligations, today):
        db.add(d)

    contract.status = ContractStatus.analyzed
    contract.risk_level = _level(level)
    contract.risk_score = score
    contract.contract_type = val(fields.contract_type)
    contract.expiration_date = val(fields.expiration_date)
    contract.analyzed_at = datetime.now(UTC)
    contract.error_message = None
    document.page_count = extracted.page_count
    document.char_count = len(full_text)
    document.has_page_numbers = extracted.has_page_numbers
    document.extraction_method = extracted.method
    document.extraction_warnings = sorted(set((document.extraction_warnings or []) + extracted.warnings))
    db.flush()

    recipients = [contract.created_by] if contract.created_by else []
    if not recipients:
        recipients = list(db.scalars(select(WorkspaceMembership.user_id).where(WorkspaceMembership.workspace_id == ws)))
    for uid in recipients:
        notifications.notify(db, uid, ws, "analysis_complete", f"Analysis complete: {contract.title}",
                             f"Overall risk level: {level}. {len(findings)} finding(s) to review.",
                             f"/app/contracts/{contract.id}")
    return result


def _obligation_row(ws: uuid.UUID, contract_id: uuid.UUID, ob: ObligationItem, source: str, verified: bool) -> Obligation:
    return Obligation(workspace_id=ws, contract_id=contract_id, party=(ob.party or None) and ob.party[:200],
                      description=ob.description, obligation_type=ob.obligation_type, due_description=ob.due_description,
                      trigger_condition=ob.trigger_condition, consequence=ob.consequence,
                      page_number=ob.evidence.page, section_ref=ob.evidence.section, excerpt=ob.evidence.quote,
                      source=source, evidence_verified=verified)


def _rule_summary(fields: ExtractedFields, findings: list[Finding], doc: ExtractedDocument) -> str:
    parts = []
    kind = fields.contract_type.value if fields.contract_type else "contract"
    parties = fields.parties.value if fields.parties else []
    names = " and ".join(p["name"] for p in parties[:2]) if parties else None
    parts.append(f"This appears to be a {kind.lower() if kind else 'contract'}" + (f" between {names}." if names else "."))
    if fields.effective_date:
        parts.append(f"It takes effect on {fields.effective_date.value.isoformat()}.")
    if fields.expiration_date:
        parts.append(f"The detected end date is {fields.expiration_date.value.isoformat()}.")
    if fields.auto_renewal and fields.auto_renewal.value:
        parts.append("It contains automatic renewal wording.")
    if fields.governing_law:
        parts.append(f"It is governed by the law of {fields.governing_law.value}.")
    serious = [f for f in findings if f.severity in ("critical", "high")]
    parts.append(f"The rule engine flagged {len(findings)} point(s) for review, {len(serious)} of them high or critical.")
    parts.append("This summary was generated by deterministic rules (no AI provider was used).")
    return " ".join(parts)
