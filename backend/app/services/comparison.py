"""Deterministic comparison of two analysed contracts (A = baseline, B = compared document).

Clauses are aligned by category and text similarity. Two clauses are reported as "identical" only when their
normalised text is equal; any other match is "modified" with a similarity score and word-level diff, so the tool
never claims equivalence that the text does not support.
"""

from __future__ import annotations

import difflib
import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AnalysisResult, Contract, ExtractedClause, Obligation, RiskFinding
from app.services.grounding import norm
from app.services.rules import CATEGORY_LABELS, SEVERITY_ORDER

TOPICS = [
    ("parties", "Parties"), ("payment_terms", "Payment terms"), ("late_payment_penalties", "Late payment"),
    ("renewal", "Renewal conditions"), ("notice_period_days", "Notice period (days)"),
    ("effective_date", "Effective date"), ("expiration_date", "Expiration date"),
    ("termination", "Termination rights"), ("liability", "Liability"), ("confidentiality", "Confidentiality"),
    ("intellectual_property", "Intellectual property"), ("governing_law", "Governing law"),
    ("jurisdiction", "Jurisdiction"),
]


def _word_diff(a: str, b: str, limit: int = 400) -> list[dict[str, str]]:
    wa, wb = a.split(), b.split()
    out: list[dict[str, str]] = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, wa, wb, autojunk=False).get_opcodes():
        if op == "equal":
            out.append({"op": "equal", "text": " ".join(wa[i1:i2])})
        else:
            if i2 > i1:
                out.append({"op": "delete", "text": " ".join(wa[i1:i2])})
            if j2 > j1:
                out.append({"op": "insert", "text": " ".join(wb[j1:j2])})
        if sum(len(x["text"].split()) for x in out) > limit:
            out.append({"op": "equal", "text": "…"})
            break
    return out


def _clause_view(c: ExtractedClause) -> dict[str, Any]:
    return {"id": str(c.id), "category": c.category, "category_label": CATEGORY_LABELS.get(c.category, c.category),
            "section_ref": c.section_ref, "heading": c.heading, "page": c.page_number, "text": c.text[:1500]}


def _topic_value(topic: str, an: AnalysisResult | None, clauses: list[ExtractedClause]) -> Any:
    if an is None:
        return None
    if topic == "parties":
        return ", ".join(f"{p.get('name')}" + (f" ({p['role']})" if p.get("role") else "") for p in an.parties) or None
    if topic == "renewal":
        if an.auto_renewal is None and not an.renewal_terms:
            return None
        return ("Automatic renewal. " if an.auto_renewal else "") + (an.renewal_terms or "")
    if topic in ("termination", "liability", "confidentiality", "intellectual_property"):
        texts = [c.text for c in clauses if c.category == topic]
        return "\n".join(texts)[:1500] or None
    v = getattr(an, topic, None)
    return v.isoformat() if isinstance(v, date | datetime) else v


def _align(a: list[ExtractedClause], b: list[ExtractedClause]) -> list[tuple[ExtractedClause | None, ExtractedClause | None, float]]:
    pairs: list[tuple[float, int, int]] = []
    na = [norm(c.text) for c in a]
    nb = [norm(c.text) for c in b]
    for i, ca in enumerate(a):
        for j, cb in enumerate(b):
            same_cat = ca.category == cb.category
            heading_match = bool(ca.heading and cb.heading and norm(ca.heading) == norm(cb.heading))
            if not (same_cat or heading_match):
                continue
            sm = difflib.SequenceMatcher(None, na[i], nb[j], autojunk=False)
            if (sm.real_quick_ratio() < 0.3 or sm.quick_ratio() < 0.3) and not heading_match:
                continue
            ratio = sm.ratio() + (0.15 if heading_match else 0) + (0.05 if ca.section_ref and ca.section_ref == cb.section_ref else 0)
            if ratio >= 0.4 or (heading_match and ca.category != "general"):
                pairs.append((ratio, i, j))
    pairs.sort(reverse=True)
    used_a: set[int] = set()
    used_b: set[int] = set()
    out: list[tuple[ExtractedClause | None, ExtractedClause | None, float]] = []
    for _ratio, i, j in pairs:
        if i in used_a or j in used_b:
            continue
        used_a.add(i)
        used_b.add(j)
        true_ratio = difflib.SequenceMatcher(None, na[i], nb[j], autojunk=False).ratio()
        out.append((a[i], b[j], true_ratio))
    out += [(a[i], None, 0.0) for i in range(len(a)) if i not in used_a]
    out += [(None, b[j], 0.0) for j in range(len(b)) if j not in used_b]
    return out


def compare(db: Session, workspace_id: uuid.UUID, a: Contract, b: Contract) -> dict[str, Any]:
    def load(c: Contract) -> tuple[AnalysisResult | None, list[ExtractedClause], list[RiskFinding], list[Obligation]]:
        an = db.scalar(select(AnalysisResult).where(AnalysisResult.contract_id == c.id, AnalysisResult.workspace_id == workspace_id))
        cl = list(db.scalars(select(ExtractedClause).where(ExtractedClause.contract_id == c.id, ExtractedClause.workspace_id == workspace_id).order_by(ExtractedClause.position)))
        fi = list(db.scalars(select(RiskFinding).where(RiskFinding.contract_id == c.id, RiskFinding.workspace_id == workspace_id)))
        ob = list(db.scalars(select(Obligation).where(Obligation.contract_id == c.id, Obligation.workspace_id == workspace_id)))
        return an, cl, fi, ob

    an_a, cl_a, fi_a, ob_a = load(a)
    an_b, cl_b, fi_b, ob_b = load(b)

    topics = []
    for key, label in TOPICS:
        va, vb = _topic_value(key, an_a, cl_a), _topic_value(key, an_b, cl_b)
        if va is None and vb is None:
            status = "not_found_in_either"
        elif va is None:
            status = "only_in_b"
        elif vb is None:
            status = "only_in_a"
        elif norm(str(va)) == norm(str(vb)):
            status = "identical"
        else:
            status = "different"
        topics.append({"key": key, "label": label, "a": va, "b": vb, "status": status})

    aligned = _align(cl_a, cl_b)
    clauses = {"added": [], "removed": [], "modified": [], "identical": []}  # type: dict[str, list[dict[str, Any]]]
    for ca, cb, ratio in aligned:
        if ca and cb:
            if norm(ca.text) == norm(cb.text):
                clauses["identical"].append({"a": _clause_view(ca), "b": _clause_view(cb)})
            else:
                clauses["modified"].append({"a": _clause_view(ca), "b": _clause_view(cb), "similarity": round(ratio, 2),
                                            "diff": _word_diff(ca.text, cb.text)})
        elif ca:
            clauses["removed"].append(_clause_view(ca))
        elif cb:
            clauses["added"].append(_clause_view(cb))
    clauses["modified"].sort(key=lambda m: m["similarity"])

    def fkey(f: RiskFinding) -> str:
        return f.rule_id or f"{f.category}:{norm(f.title)}"

    map_a = {fkey(f): f for f in fi_a}
    map_b = {fkey(f): f for f in fi_b}
    sev = {s: i for i, s in enumerate(SEVERITY_ORDER)}

    def fview(f: RiskFinding) -> dict[str, Any]:
        return {"title": f.title, "severity": f.severity.value, "category": f.category, "source": f.source,
                "section": f.section_ref, "page": f.page_number, "excerpt": (f.excerpt or "")[:400]}

    new_in_b = [fview(f) for k, f in map_b.items() if k not in map_a]
    resolved_in_b = [fview(f) for k, f in map_a.items() if k not in map_b]
    changed = [{"title": map_b[k].title, "a": map_a[k].severity.value, "b": map_b[k].severity.value,
                "worse_in_b": sev[map_b[k].severity.value] < sev[map_a[k].severity.value]}
               for k in map_a.keys() & map_b.keys() if map_a[k].severity != map_b[k].severity]

    unfavourable = [f"New {r['severity']} risk in B: {r['title']}" for r in new_in_b if r["severity"] in ("critical", "high", "medium")]
    unfavourable += [f"Risk rated higher in B: {c['title']} ({c['a']} → {c['b']})" for c in changed if c["worse_in_b"]]
    if an_a and an_b and an_b.risk_score > an_a.risk_score:
        unfavourable.append(f"Overall risk score is higher in B ({an_a.risk_score} → {an_b.risk_score})")
    important = [f"{t['label']}: values differ" for t in topics if t["status"] == "different"]
    important += [f"{t['label']}: only found in {'A' if t['status'] == 'only_in_a' else 'B'}"
                  for t in topics if t["status"] in ("only_in_a", "only_in_b")]

    def obl_summary(obs: list[Obligation]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for o in obs:
            counts[o.party or "Unspecified"] = counts.get(o.party or "Unspecified", 0) + 1
        return counts

    return {
        "a": {"id": str(a.id), "title": a.title, "risk_score": an_a.risk_score if an_a else None,
              "risk_level": an_a.risk_level.value if an_a else None, "analyzed": an_a is not None},
        "b": {"id": str(b.id), "title": b.title, "risk_score": an_b.risk_score if an_b else None,
              "risk_level": an_b.risk_level.value if an_b else None, "analyzed": an_b is not None},
        "topics": topics,
        "clauses": clauses,
        "risks": {"new_in_b": new_in_b, "resolved_in_b": resolved_in_b, "severity_changes": changed},
        "obligations": {"a": obl_summary(ob_a), "b": obl_summary(ob_b)},
        "important_differences": important,
        "potentially_unfavourable": unfavourable,
        "method": ("Deterministic comparison. Clauses are matched by category, heading and text similarity. 'Identical' "
                   "means the normalised text is the same; anything else is reported as modified with a similarity score. "
                   "Matching is heuristic and may pair clauses imperfectly - verify important differences in the documents."),
    }
