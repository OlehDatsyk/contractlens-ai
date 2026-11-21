"""Report generation (PDF, CSV, JSON) from one contract's analysis.

The report data is built exclusively from rows scoped to the caller's workspace and the selected contract.
"""

from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import UTC, datetime
from typing import Any

# escape() only escapes text for ReportLab paragraph markup; no XML is ever parsed.
from xml.sax.saxutils import escape  # nosec B406

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AnalysisResult, Contract, Deadline, Document, Obligation, RiskFinding
from app.services.rules import CATEGORY_LABELS, RISK_CATEGORIES, SEVERITY_ORDER

DISCLAIMER = ("ContractLens AI is an informational document analysis tool. It is not a law firm and does not provide "
              "legal advice. Automated and AI analysis may be incomplete or incorrect. Verify important findings in the "
              "original document and have important legal decisions reviewed by a qualified legal professional.")


def build_report_data(db: Session, workspace_id: uuid.UUID, contract: Contract) -> dict[str, Any]:
    an = db.scalar(select(AnalysisResult).where(AnalysisResult.contract_id == contract.id, AnalysisResult.workspace_id == workspace_id))
    doc = db.scalar(select(Document).where(Document.contract_id == contract.id, Document.workspace_id == workspace_id))
    findings = list(db.scalars(select(RiskFinding).where(RiskFinding.contract_id == contract.id, RiskFinding.workspace_id == workspace_id)))
    findings.sort(key=lambda f: (SEVERITY_ORDER.index(f.severity.value), f.page_number or 0))
    obligations = list(db.scalars(select(Obligation).where(Obligation.contract_id == contract.id, Obligation.workspace_id == workspace_id)))
    deadlines = list(db.scalars(select(Deadline).where(Deadline.contract_id == contract.id, Deadline.workspace_id == workspace_id).order_by(Deadline.due_date)))

    def d(v: Any) -> Any:
        return v.isoformat() if hasattr(v, "isoformat") else v

    overview = {
        "title": contract.title, "status": contract.status.value,
        "file_name": doc.original_filename if doc else None, "pages": doc.page_count if doc else None,
        "analysed_at": d(contract.analyzed_at), "engine": an.engine if an else None,
        "ai_used": an.ai_used if an else False,
    }
    key_info = {}
    if an:
        key_info = {
            "Contract title": an.contract_title, "Contract type": an.contract_type,
            "Parties": "; ".join(f"{p.get('name')}" + (f" ({p.get('role')})" if p.get("role") else "") for p in an.parties) or None,
            "Effective date": d(an.effective_date), "Expiration date": d(an.expiration_date),
            "Term": an.term_description, "Renewal": an.renewal_terms,
            "Automatic renewal": None if an.auto_renewal is None else ("Yes" if an.auto_renewal else "No"),
            "Notice period (days)": an.notice_period_days, "Governing law": an.governing_law,
            "Jurisdiction": an.jurisdiction,
        }
    financial = {}
    if an:
        financial = {"Payment terms": an.payment_terms, "Late payment": an.late_payment_penalties,
                     "Price adjustment": an.price_adjustment,
                     "Fees": [{"amount": f.get("amount"), "description": f.get("description"), "page": f.get("page"),
                               "section": f.get("section")} for f in an.fees[:20]],
                     "Payment deadlines": [{"days": p.get("days"), "description": p.get("description"), "page": p.get("page"),
                                            "section": p.get("section")} for p in an.payment_deadlines]}
    clauses = []
    if an:
        for key, v in an.clause_overview.items():
            clauses.append({"clause": CATEGORY_LABELS.get(key, key), "present": v.get("present"), "summary": v.get("summary"),
                            "excerpt": v.get("excerpt"), "page": v.get("page"), "section": v.get("section")})
    risks = [{"severity": f.severity.value, "title": f.title, "category": RISK_CATEGORIES.get(f.category, f.category),
              "description": f.description, "explanation": f.explanation, "excerpt": f.excerpt, "page": f.page_number,
              "section": f.section_ref, "source": f.source, "evidence_verified": f.evidence_verified,
              "suggested_actions": f.suggested_actions} for f in findings]
    review_points = []
    for f in findings:
        if f.severity.value in ("critical", "high", "medium") and f.suggested_actions:
            ref = ", ".join(x for x in [f"section {f.section_ref}" if f.section_ref else None, f"page {f.page_number}" if f.page_number else None] if x)
            review_points.append(f"{f.title}{f' ({ref})' if ref else ''}: {f.suggested_actions[0]}")
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "disclaimer": DISCLAIMER,
        "overview": overview,
        "summary": an.summary if an else None,
        "risk": {"score": an.risk_score if an else None, "level": an.risk_level.value if an else None,
                 "method": (an.score_breakdown or {}).get("method") if an else None,
                 "counts": (an.score_breakdown or {}).get("counts") if an else None},
        "key_information": key_info,
        "financial": financial,
        "clauses": clauses,
        "risks": risks,
        "obligations": [{"party": o.party, "description": o.description, "type": o.obligation_type, "due": o.due_description,
                         "trigger": o.trigger_condition, "consequence": o.consequence, "page": o.page_number,
                         "section": o.section_ref, "source": o.source, "evidence_verified": o.evidence_verified}
                        for o in obligations[:100]],
        "deadlines": [{"title": dl.title, "type": dl.deadline_type, "due_date": dl.due_date.isoformat(), "status": dl.status,
                       "source": dl.source} for dl in deadlines],
        "review_points": review_points[:25],
    }


def render_json(data: dict[str, Any]) -> bytes:
    return json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")


def _csv_safe(value: Any) -> str:
    """Prevent CSV/formula injection when the file is opened in a spreadsheet."""
    s = "" if value is None else str(value)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def render_csv(data: dict[str, Any]) -> bytes:
    buf = io.StringIO()
    raw = csv.writer(buf)

    class _Safe:
        @staticmethod
        def writerow(row: list[Any]) -> None:
            raw.writerow([_csv_safe(x) for x in row])

    w = _Safe()
    w.writerow(["section", "item", "value", "severity", "page", "clause", "source"])
    for k, v in data["overview"].items():
        w.writerow(["overview", k, v, "", "", "", ""])
    w.writerow(["risk", "score", data["risk"]["score"], data["risk"]["level"], "", "", ""])
    for k, v in data["key_information"].items():
        w.writerow(["key_information", k, v, "", "", "", ""])
    for k, v in data["financial"].items():
        if isinstance(v, list):
            for item in v:
                w.writerow(["financial", k, item.get("description"), "", item.get("page"), item.get("section"), ""])
        else:
            w.writerow(["financial", k, v, "", "", "", ""])
    for c in data["clauses"]:
        w.writerow(["clause", c["clause"], c.get("summary") or ("present" if c["present"] else "not found"), "", c.get("page"), c.get("section"), ""])
    for r in data["risks"]:
        w.writerow(["risk_finding", r["title"], r["description"], r["severity"], r["page"], r["section"], r["source"]])
    for o in data["obligations"]:
        w.writerow(["obligation", o["party"], o["description"], "", o["page"], o["section"], o["source"]])
    for dl in data["deadlines"]:
        w.writerow(["deadline", dl["title"], dl["due_date"], "", "", "", dl["source"]])
    w.writerow(["disclaimer", "", data["disclaimer"], "", "", "", ""])
    return ("﻿" + buf.getvalue()).encode("utf-8")


def render_pdf(data: dict[str, Any]) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
                            title=f"ContractLens report - {data['overview']['title']}", author="ContractLens AI")
    ss = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=ss["BodyText"], fontSize=9.5, leading=13, alignment=TA_LEFT)
    small = ParagraphStyle("small", parent=body, fontSize=8, leading=10.5, textColor=colors.HexColor("#475569"))
    h1 = ParagraphStyle("h1", parent=ss["Heading1"], fontSize=18, textColor=colors.HexColor("#1e293b"))
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], fontSize=13, textColor=colors.HexColor("#3730a3"), spaceBefore=10)
    sev_colors = {"critical": "#991b1b", "high": "#c2410c", "medium": "#a16207", "low": "#15803d", "informational": "#475569"}

    def p(text: Any, style: ParagraphStyle = body) -> Paragraph:
        return Paragraph(escape("" if text is None else str(text)).replace("\n", "<br/>"), style)

    def ref(item: dict[str, Any]) -> str:
        parts = [f"Section {item['section']}" if item.get("section") else None, f"Page {item['page']}" if item.get("page") else None]
        return " · ".join(x for x in parts if x) or "No page reference available"

    story: list[Any] = [p("ContractLens AI — Contract analysis report", small), Paragraph(escape(data["overview"]["title"]), h1)]
    story.append(p(f"Generated {data['generated_at']} · Engine: {data['overview'].get('engine') or 'n/a'}", small))
    story.append(Spacer(1, 6))
    story.append(Table([[p(data["disclaimer"], small)]], colWidths=[174 * mm],
                       style=TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f1f5f9")),
                                         ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
                                         ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6)])))

    story.append(Paragraph("Overview", h2))
    risk = data["risk"]
    story.append(p(f"Risk level: {(risk['level'] or 'not analysed').upper()} · Score: {risk['score'] if risk['score'] is not None else '—'}/100"))
    if data.get("summary"):
        story.append(Spacer(1, 4))
        story.append(p(data["summary"]))
    if risk.get("method"):
        story.append(Spacer(1, 4))
        story.append(p("How the score is calculated: " + risk["method"], small))

    def kv_table(items: dict[str, Any]) -> Table:
        rows = [[p(k, small), p(v if v not in (None, "") else "Not found in document")] for k, v in items.items() if not isinstance(v, list)]
        t = Table(rows, colWidths=[45 * mm, 129 * mm])
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#e2e8f0"))]))
        return t

    if data["key_information"]:
        story.append(Paragraph("Key information", h2))
        story.append(kv_table(data["key_information"]))
    if data["financial"]:
        story.append(Paragraph("Financial obligations", h2))
        story.append(kv_table(data["financial"]))
        for fee in data["financial"].get("Fees", [])[:12]:
            story.append(p(f"• {fee.get('amount')}: {fee.get('description')} ({ref(fee)})", small))

    if data["risks"]:
        story.append(Paragraph("Risk summary", h2))
        for r in data["risks"]:
            colour = sev_colors.get(r["severity"], "#475569")
            block = [Paragraph(f"<font color='{colour}'><b>{escape(r['severity'].upper())}</b></font> &nbsp; <b>{escape(r['title'])}</b>"
                               f" <font size=8 color='#64748b'>({escape(r['category'])}; {'rule' if r['source'] == 'rule' else 'AI' + ('' if r['evidence_verified'] else ', evidence unverified')})</font>", body),
                     p(r["description"])]
            if r.get("excerpt"):
                block.append(p(f"“{r['excerpt']}” — {ref(r)}", small))
            if r.get("suggested_actions"):
                block.append(p("Suggested: " + "; ".join(r["suggested_actions"][:3]), small))
            block.append(Spacer(1, 5))
            story.append(KeepTogether(block))

    if data["clauses"]:
        story.append(Paragraph("Important clauses", h2))
        rows = [[p("Clause", small), p("Found", small), p("Summary / excerpt", small), p("Reference", small)]]
        for c in data["clauses"]:
            rows.append([p(c["clause"], small), p("Yes" if c["present"] else "No", small),
                         p((c.get("summary") or c.get("excerpt") or "")[:500], small), p(ref(c) if c["present"] else "—", small)])
        t = Table(rows, colWidths=[32 * mm, 14 * mm, 98 * mm, 30 * mm], repeatRows=1)
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2ff")),
                               ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#e2e8f0"))]))
        story.append(t)

    if data["obligations"]:
        story.append(Paragraph("Obligations", h2))
        for o in data["obligations"][:40]:
            story.append(p(f"• {o['party'] or 'Unspecified party'}: {o['description']}" + (f" — Due: {o['due']}" if o.get("due") else "") + f" ({ref(o)})", small))
    if data["deadlines"]:
        story.append(Paragraph("Deadlines", h2))
        for dl in data["deadlines"]:
            story.append(p(f"• {dl['due_date']} — {dl['title']} ({dl['type']}, {dl['status']})", small))
    if data["review_points"]:
        story.append(Paragraph("Recommended review points", h2))
        for rp in data["review_points"]:
            story.append(p("• " + rp, small))

    def footer(canvas: Any, d: Any) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.HexColor("#64748b"))
        canvas.drawString(18 * mm, 10 * mm, "Confidential · Generated by ContractLens AI · Not legal advice")
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {d.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
