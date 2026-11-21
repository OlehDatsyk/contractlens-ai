"""Deterministic, explainable contract analysis.

Everything here is rule-based: regular expressions and keyword heuristics with the matched text kept as
evidence. These results are always available (no AI provider required) and are labelled "rule" in the UI.
Heuristics can miss or misread clauses; they are a review aid, not a legal assessment.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.services.extraction import Clause, section_label

# --- Clause classification ------------------------------------------------------------------------------------

CLAUSE_CATEGORIES: dict[str, dict[str, list[str]]] = {
    "parties": {"h": ["parties", "recitals", "background"], "b": []},
    "term": {"h": ["term", "duration", "commencement", "effective date"], "b": ["effective date", "shall commence", "term of this agreement"]},
    "renewal": {"h": ["renewal", "extension"], "b": ["automatically renew", "auto-renew", "renewal term", "successive"]},
    "payment": {"h": ["payment", "fees", "compensation", "invoic", "price", "charges", "remuneration", "consideration"],
                "b": ["invoice", "shall pay", "payment", "fee", "net 30", "within thirty"]},
    "confidentiality": {"h": ["confidential", "non-disclosure", "nondisclosure"], "b": ["confidential information", "shall not disclose"]},
    "intellectual_property": {"h": ["intellectual property", "ownership", "ip rights", "work product", "license", "licence"],
                              "b": ["intellectual property", "copyright", "work made for hire", "all right, title and interest", "patent"]},
    "liability": {"h": ["limitation of liability", "liability"], "b": ["shall not be liable", "aggregate liability", "consequential damages", "limitation of liability"]},
    "indemnification": {"h": ["indemn"], "b": ["indemnify", "hold harmless", "defend and indemnify"]},
    "termination": {"h": ["termination", "terminate", "cancellation"], "b": ["may terminate", "terminate this agreement", "termination"]},
    "dispute_resolution": {"h": ["dispute", "arbitration", "mediation"], "b": ["arbitration", "mediation", "dispute"]},
    "governing_law": {"h": ["governing law", "applicable law", "jurisdiction"], "b": ["governed by the laws", "governed by and construed"]},
    "data_protection": {"h": ["data protection", "privacy", "personal data", "gdpr", "data processing", "security"],
                        "b": ["personal data", "gdpr", "data protection", "data subject", "processor"]},
    "non_compete": {"h": ["non-compet", "noncompet", "restrictive covenant", "non-solicit"], "b": ["shall not compete", "non-compete", "competing business", "solicit"]},
    "exclusivity": {"h": ["exclusiv"], "b": ["exclusive basis", "exclusively", "sole and exclusive provider"]},
    "warranties": {"h": ["warrant", "representation"], "b": ["warrants that", "represents and warrants", "as is", "disclaims"]},
    "force_majeure": {"h": ["force majeure"], "b": ["force majeure", "acts of god"]},
    "assignment": {"h": ["assignment", "subcontract"], "b": ["may not assign", "assign this agreement"]},
    "notices": {"h": ["notice"], "b": ["notices shall be", "notice shall be given"]},
}

CATEGORY_LABELS = {
    "parties": "Parties", "term": "Term", "renewal": "Renewal", "payment": "Payment", "confidentiality": "Confidentiality",
    "intellectual_property": "Intellectual property", "liability": "Liability", "indemnification": "Indemnification",
    "termination": "Termination", "dispute_resolution": "Dispute resolution", "governing_law": "Governing law",
    "data_protection": "Data protection", "non_compete": "Non-compete", "exclusivity": "Exclusivity",
    "warranties": "Warranties", "force_majeure": "Force majeure", "assignment": "Assignment", "notices": "Notices",
    "general": "General",
}
LEGAL_CLAUSE_KEYS = ["confidentiality", "intellectual_property", "liability", "indemnification", "termination",
                     "dispute_resolution", "data_protection", "non_compete", "exclusivity", "warranties"]


def classify_clause(clause: Clause) -> str:
    heading = (clause.heading or clause.parent_heading or "").lower()
    body = clause.text.lower()[:3000]
    best, best_score = "general", 0
    for cat, kw in CLAUSE_CATEGORIES.items():
        score = sum(4 for k in kw["h"] if k in heading) + sum(1 for k in kw["b"] if k in body)
        if score > best_score:
            best, best_score = cat, score
    # Avoid classifying "exclusive jurisdiction" as an exclusivity clause.
    if best == "exclusivity" and "jurisdiction" in body and "exclusiv" not in heading:
        best = "governing_law"
    return best if best_score >= 2 else "general"


# --- Sentences / evidence -------------------------------------------------------------------------------------

_ABBREV = re.compile(r"\b(Inc|Ltd|LLC|Co|Corp|No|Nos|Art|Sec|e\.g|i\.e|etc|Mr|Mrs|Ms|Dr|St|vs|approx|U\.S|U\.K)\.", re.I)


def _is_heading_line(line: str) -> bool:
    line = line.strip()
    return 0 < len(line.split()) <= 10 and not re.search(r"[.;:,]$", line) and bool(re.match(r"[A-Z0-9(§]", line))


def sentences(text: str) -> list[str]:
    # Keep heading lines ("4. Confidentiality") separate from the sentence that follows them.
    blocks: list[str] = []
    buf: list[str] = []
    for line in text.split("\n"):
        # A wrapped line in the middle of a sentence is not a heading.
        if _is_heading_line(line) and (not buf or re.search(r"[.;:!?]$", buf[-1])):
            if buf:
                blocks.append(" ".join(buf))
                buf = []
            blocks.append(line.strip())
        elif line.strip():
            buf.append(line.strip())
    if buf:
        blocks.append(" ".join(buf))
    out: list[str] = []
    for block in blocks:
        protected = _ABBREV.sub(lambda m: m.group(0).replace(".", "§DOT§"), block)
        parts = re.split(r"(?<=[.;!?])\s+(?=[A-Z(“\"])", protected)
        out.extend(p.replace("§DOT§", ".").strip() for p in parts if p.strip())
    return out


@dataclass
class Evidence:
    quote: str
    page: int | None
    section: str | None

    def as_dict(self) -> dict[str, Any]:
        return {"quote": self.quote[:600], "page": self.page, "section": self.section}


def _ev(sentence: str, clause: Clause) -> Evidence:
    return Evidence(sentence.strip()[:600], clause.page_number, section_label(clause.section_ref, clause.heading or clause.parent_heading))


def find_sentences(clauses: list[Clause], pattern: re.Pattern[str], categories: set[str] | None = None) -> list[tuple[str, Clause, re.Match[str]]]:
    out = []
    for c in clauses:
        if categories and c.category not in categories:
            continue
        for s in sentences(c.text):
            m = pattern.search(s)
            if m:
                out.append((s, c, m))
    return out


# --- Dates and numbers ----------------------------------------------------------------------------------------

_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
_MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
_MONTHS["sept"] = 9
_MON = r"(January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept|Sep|Oct|Nov|Dec)\.?"
_DATE_PATTERNS = [
    (re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:day\s+of\s+)?{_MON},?\s+(\d{{4}})\b", re.I), "dmy"),
    (re.compile(rf"\b{_MON}\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", re.I), "mdy"),
    (re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"), "iso"),
    (re.compile(r"\b(\d{1,2})[/.](\d{1,2})[/.](\d{4})\b"), "numeric"),
]


@dataclass
class FoundDate:
    value: date
    raw: str
    start: int
    ambiguous: bool = False


def find_dates(text: str) -> list[FoundDate]:
    found: list[FoundDate] = []
    taken: list[tuple[int, int]] = []
    for pat, kind in _DATE_PATTERNS:
        for m in pat.finditer(text):
            if any(a <= m.start() < b for a, b in taken):
                continue
            try:
                ambiguous = False
                if kind == "dmy":
                    d = date(int(m.group(3)), _MONTHS[m.group(2).lower().rstrip(".")], int(m.group(1)))
                elif kind == "mdy":
                    d = date(int(m.group(3)), _MONTHS[m.group(1).lower().rstrip(".")], int(m.group(2)))
                elif kind == "iso":
                    d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                else:
                    a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
                    if a > 12:
                        d = date(y, b, a)
                    elif b > 12:
                        d = date(y, a, b)
                    else:
                        d, ambiguous = date(y, b, a), a != b  # interpreted day/month/year, flagged
            except (ValueError, KeyError):
                continue
            if 1950 <= d.year <= 2150:
                found.append(FoundDate(d, m.group(0), m.start(), ambiguous))
                taken.append((m.start(), m.end()))
    return sorted(found, key=lambda f: f.start)


_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "fourteen": 14, "fifteen": 15, "eighteen": 18, "twenty": 20, "twenty-four": 24,
    "thirty": 30, "thirty-six": 36, "forty-five": 45, "sixty": 60, "ninety": 90, "hundred twenty": 120,
    "one hundred twenty": 120, "one hundred and twenty": 120, "a": 1, "an": 1,
}
_NUM = r"(\d{1,4}|" + "|".join(sorted((re.escape(k) for k in _NUMBER_WORDS if len(k) > 2), key=len, reverse=True)) + r")"
_NUM_PAREN = _NUM + r"(?:\s*\(\d{1,4}\))?"


def to_int(token: str) -> int | None:
    token = token.lower().strip()
    if token.isdigit():
        return int(token)
    return _NUMBER_WORDS.get(token)


def add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


_AMOUNT = re.compile(
    r"(?:(?:USD|EUR|GBP|CHF|CAD|AUD|US\$|A\$|C\$|\$|€|£)\s?\d{1,3}(?:[,\s]\d{3})*(?:\.\d{1,2})?(?:\s?(?:million|thousand|k|m)\b)?"
    r"|\b\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?\s?(?:USD|EUR|GBP|CHF|dollars|euros|pounds)\b)",
    re.I,
)
_PERCENT = re.compile(r"(\d{1,2}(?:\.\d{1,2})?)\s?(?:%|percent|per cent)", re.I)


# --- Field extraction -----------------------------------------------------------------------------------------

CONTRACT_TYPES = [
    (r"non[- ]?disclosure|confidentiality agreement|\bnda\b", "Non-disclosure agreement"),
    (r"master services|professional services|services agreement|service agreement", "Services agreement"),
    (r"statement of work|\bsow\b", "Statement of work"),
    (r"software as a service|\bsaas\b|subscription", "SaaS / subscription agreement"),
    (r"employment", "Employment agreement"),
    (r"independent contractor|freelanc|contractor agreement", "Independent contractor agreement"),
    (r"consult", "Consulting agreement"),
    (r"licen[cs]e", "License agreement"),
    (r"\blease\b|tenancy|rental", "Lease agreement"),
    (r"supply|purchase|procurement", "Supply / purchase agreement"),
    (r"partnership|joint venture", "Partnership agreement"),
    (r"loan|credit", "Loan agreement"),
    (r"data processing", "Data processing agreement"),
    (r"terms of service|terms and conditions", "Terms and conditions"),
]

ROLE_WORDS = ["Client", "Customer", "Contractor", "Consultant", "Provider", "Service Provider", "Supplier", "Vendor",
              "Company", "Employer", "Employee", "Licensor", "Licensee", "Landlord", "Tenant", "Buyer", "Seller",
              "Purchaser", "Agency", "Freelancer", "Discloser", "Recipient", "Disclosing Party", "Receiving Party",
              "Partner", "Lender", "Borrower", "Processor", "Controller"]


@dataclass
class FieldResult:
    value: Any
    evidence: Evidence | None = None
    note: str | None = None
    source: str = "rule"

    def evidence_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"source": self.source, "verified": self.evidence is not None}
        if self.evidence:
            d.update(self.evidence.as_dict())
        if self.note:
            d["note"] = self.note
        return d


@dataclass
class ExtractedFields:
    contract_title: FieldResult | None = None
    contract_type: FieldResult | None = None
    parties: FieldResult | None = None
    effective_date: FieldResult | None = None
    expiration_date: FieldResult | None = None
    term_description: FieldResult | None = None
    renewal_terms: FieldResult | None = None
    auto_renewal: FieldResult | None = None
    notice_period_days: FieldResult | None = None
    governing_law: FieldResult | None = None
    jurisdiction: FieldResult | None = None
    payment_terms: FieldResult | None = None
    payment_deadlines: list[dict[str, Any]] = field(default_factory=list)
    late_payment_penalties: FieldResult | None = None
    fees: list[dict[str, Any]] = field(default_factory=list)
    financial_obligations: list[dict[str, Any]] = field(default_factory=list)
    price_adjustment: FieldResult | None = None
    clause_overview: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _clean_name(name: str) -> str:
    name = re.sub(r"\s+", " ", name).strip(" ,;:.\"“”'")
    name = re.sub(r"^(?:the\s+)?(?:undersigned\s+)?", "", name, flags=re.I)
    return name[:150]


def extract_parties(text: str, clauses: list[Clause]) -> FieldResult | None:
    head = text[:4000]
    parties: list[dict[str, Any]] = []
    m = re.search(r"\bbetween\s*:?\s+(.{3,600}?)\s+(?:\(|,)?\s*and\s+(.{3,400}?)(?:\.\s|\n\n|;|\bwhereas\b|\bcollectively\b)", head, re.I | re.S)
    evidence_text = None
    if m:
        evidence_text = m.group(0)
        for chunk in (m.group(1), m.group(2)):
            role = None
            rm = re.search(r"\((?:hereinafter\s+)?(?:referred to as\s+)?(?:the\s+)?[\"“']([^\"”']{2,40})[\"”']\)", chunk, re.I)
            if rm:
                role = rm.group(1).strip()
            name = re.split(r"\(|,\s*(?:a|an)\s|,\s*(?:with|having|located|registered|whose)\b|,\s*\d", chunk, maxsplit=1)[0]
            name = _clean_name(name)
            if 1 < len(name) <= 150 and not re.match(r"^(the )?parties$", name, re.I):
                parties.append({"name": name, "role": role})
    if not parties:
        for role in ROLE_WORDS:
            lm = re.search(rf"^\s*{re.escape(role)}\s*:\s*(.{{2,120}})$", head, re.I | re.M)
            if lm:
                parties.append({"name": _clean_name(lm.group(1)), "role": role})
                evidence_text = evidence_text or lm.group(0)
    if not parties:
        return None
    first = next((c for c in clauses if evidence_text and evidence_text[:40] in c.text), clauses[0] if clauses else None)
    ev = Evidence(re.sub(r"\s+", " ", evidence_text or "")[:600], first.page_number if first else None,
                  section_label(first.section_ref, first.heading) if first else None)
    return FieldResult(parties[:6], ev)


def _first_with_date(clauses: list[Clause], pattern: re.Pattern[str], categories: set[str] | None = None,
                     after: date | None = None) -> FieldResult | None:
    for sent, clause, _m in find_sentences(clauses, pattern, categories):
        for fd in find_dates(sent):
            if after and fd.value <= after:
                continue
            note = "Ambiguous numeric date; interpreted as day/month/year" if fd.ambiguous else None
            return FieldResult(fd.value, _ev(sent, clause), note)
    return None


_EFFECTIVE = re.compile(r"effective\s+(?:date|as\s+of|from|on)|commenc|entered\s+into\s+(?:on|as\s+of)|\bdated\b|made\s+(?:on|as\s+of)|start\s+date", re.I)
_EXPIRY = re.compile(r"expir|terminat\w*\s+on|end\w*\s+on|\buntil\b|\bthrough\b|valid\s+(?:until|through)|end\s+date", re.I)
_TERM_LEN = re.compile(rf"(?:term|period|duration)\s+of\s+{_NUM_PAREN}\s*(year|month|week|day)s?", re.I)
_AUTO_RENEW = re.compile(r"automatic(?:ally)?\s+(?:be\s+)?renew|auto[- ]?renew|renew\s+automatically|successive\s+(?:renewal\s+)?(?:periods?|terms?)|tacitly\s+renew", re.I)
_RENEW = re.compile(r"renew|extension|extend", re.I)
_NOTICE = re.compile(rf"{_NUM_PAREN}\s*(day|month|week)s?['’]?\s+(?:prior\s+|advance\s+)?(?:written\s+)?notice|notice\s+(?:period\s+)?of\s+(?:at\s+least\s+)?{_NUM_PAREN}\s*(day|month|week)s?", re.I)
_GOV_LAW = re.compile(r"governed\s+by\s+(?:and\s+construed\s+(?:in\s+accordance\s+with\s+)?)?(?:the\s+)?laws?\s+of\s+(?:the\s+)?([A-Z][A-Za-z .,'&-]{1,80}?)(?=[,.;]|\s+(?:and\s+(?:the|any|each|all|shall|its|their|is|are|will|construed|interpreted)|without|excluding|with)\b|$)", re.I)
_JURIS = re.compile(r"(?:exclusive|non-exclusive)?\s*jurisdiction\s+of\s+the\s+(?:competent\s+)?courts?\s+(?:of|in|located\s+in|sitting\s+in)\s+(?:the\s+)?([A-Z][A-Za-z .,'&-]{1,80}?)(?=[,.;]|\s+(?:and|for|with)\b|$)|courts?\s+of\s+([A-Z][A-Za-z .'&-]{1,60}?)\s+shall\s+have\s+(?:exclusive\s+)?jurisdiction", re.I)
_PAY_WITHIN = re.compile(rf"within\s+{_NUM_PAREN}\s*(?:calendar\s+|business\s+|working\s+)?(day)s?\s+(?:of|after|from|following)\s+(?:the\s+)?(?:date\s+of\s+|receipt\s+of\s+)?(?:the\s+|an?\s+|each\s+)?(invoice|receipt|delivery|completion|acceptance|month|end)|\bnet\s*[- ]?(\d{{1,3}})\b", re.I)
_LATE = re.compile(r"\blate\b|overdue|past\s+due|interest\s+(?:at|of)|late\s+payment|delay\w*\s+in\s+payment", re.I)
_PRICE_ADJ = re.compile(r"(?:increase|adjust|revise|change)\w*\s+(?:the\s+|its\s+|any\s+)?(?:fees|prices?|rates?|charges)|(?:fees|prices?|rates?|charges)\s+(?:may|shall|will)\s+(?:be\s+)?(?:increase|adjust|revis)|consumer\s+price\s+index|\bcpi\b|indexation|inflation", re.I)


def extract_fields(full_text: str, clauses: list[Clause], today: date | None = None) -> ExtractedFields:
    f = ExtractedFields()
    first_lines = [ln.strip() for ln in full_text.split("\n")[:12] if ln.strip()]
    title_line = next((ln for ln in first_lines if re.search(r"agreement|contract|terms|statement of work|lease|licen[cs]e|addendum|order|memorandum|deed", ln, re.I) and len(ln) <= 150), None)
    if title_line:
        c0 = clauses[0] if clauses else None
        f.contract_title = FieldResult(title_line.strip(" .").title() if title_line.isupper() else title_line.strip(" ."),
                                       Evidence(title_line, c0.page_number if c0 else None, None))
    type_source = (title_line or "") + " " + full_text[:1500]
    for pattern, label in CONTRACT_TYPES:
        if re.search(pattern, type_source, re.I):
            f.contract_type = FieldResult(label, None, "Inferred from the document title and opening text")
            break

    f.parties = extract_parties(full_text, clauses)

    f.effective_date = _first_with_date(clauses, _EFFECTIVE)
    eff = f.effective_date.value if f.effective_date else None
    f.expiration_date = _first_with_date(clauses, _EXPIRY, {"term", "renewal", "termination", "general"}, after=eff)
    term_hits = find_sentences(clauses, _TERM_LEN, {"term", "renewal", "general", "termination"})
    if term_hits:
        sent, clause, m = term_hits[0]
        f.term_description = FieldResult(sent[:400], _ev(sent, clause))
        n = to_int(m.group(1))
        unit = m.group(2).lower()
        if f.expiration_date is None and eff and n:
            months = n * 12 if unit == "year" else n if unit == "month" else None
            if months:
                f.expiration_date = FieldResult(add_months(eff, months), _ev(sent, clause),
                                                "Calculated from the effective date and the stated term length")

    auto = find_sentences(clauses, _AUTO_RENEW)
    if auto:
        sent, clause, _ = auto[0]
        f.auto_renewal = FieldResult(True, _ev(sent, clause))
        f.renewal_terms = FieldResult(sent[:600], _ev(sent, clause))
    else:
        ren = find_sentences(clauses, _RENEW, {"renewal", "term"})
        if ren:
            sent, clause, _ = ren[0]
            f.renewal_terms = FieldResult(sent[:600], _ev(sent, clause))
            f.auto_renewal = FieldResult(False, _ev(sent, clause), "Renewal is mentioned but no automatic renewal wording was found")

    notice_hits = find_sentences(clauses, _NOTICE, {"termination", "renewal", "term", "general"})
    for sent, clause, m in notice_hits:
        n = to_int(m.group(1) or m.group(3) or "")
        unit = (m.group(2) or m.group(4) or "day").lower()
        if n:
            days = n * 30 if unit == "month" else n * 7 if unit == "week" else n
            f.notice_period_days = FieldResult(days, _ev(sent, clause), None if unit == "day" else f"Converted from {n} {unit}(s)")
            break

    gl = find_sentences(clauses, _GOV_LAW)
    if gl:
        sent, clause, m = gl[0]
        f.governing_law = FieldResult(m.group(1).strip(" ,."), _ev(sent, clause))
    jr = find_sentences(clauses, _JURIS)
    if jr:
        sent, clause, m = jr[0]
        f.jurisdiction = FieldResult((m.group(1) or m.group(2)).strip(" ,."), _ev(sent, clause))

    pay_hits = find_sentences(clauses, _PAY_WITHIN)
    for sent, clause, m in pay_hits[:8]:
        pay_days: int | None = to_int(m.group(1)) if m.group(1) else (int(m.group(4)) if m.group(4) else None)
        f.payment_deadlines.append({"description": sent[:300], "days": pay_days, **_ev(sent, clause).as_dict()})
    if pay_hits:
        sent, clause, _ = pay_hits[0]
        f.payment_terms = FieldResult(sent[:600], _ev(sent, clause))
    else:
        generic = find_sentences(clauses, re.compile(r"shall\s+pay|payable|invoice", re.I), {"payment"})
        if generic:
            sent, clause, _ = generic[0]
            f.payment_terms = FieldResult(sent[:600], _ev(sent, clause))

    late = find_sentences(clauses, _LATE, {"payment", "general"})
    late = [x for x in late if re.search(r"interest|fee|penalt|charge|suspend|%|percent", x[0], re.I)]
    if late:
        sent, clause, _ = late[0]
        f.late_payment_penalties = FieldResult(sent[:600], _ev(sent, clause))

    seen: set[str] = set()
    for c in clauses:
        for sent in sentences(c.text):
            for am in _AMOUNT.finditer(sent):
                key = am.group(0) + sent[:40]
                if key in seen:
                    continue
                seen.add(key)
                f.fees.append({"amount": am.group(0).strip(), "description": sent[:300], **_ev(sent, c).as_dict()})
                if re.search(r"shall\s+pay|agrees\s+to\s+pay|will\s+pay|payable|fee|deposit|retainer", sent, re.I):
                    f.financial_obligations.append({"description": sent[:300], "amount": am.group(0).strip(), **_ev(sent, c).as_dict()})
            if len(f.fees) >= 25:
                break
    pa = find_sentences(clauses, _PRICE_ADJ, {"payment", "general", "renewal", "term"})
    if pa:
        sent, clause, _ = pa[0]
        f.price_adjustment = FieldResult(sent[:600], _ev(sent, clause))

    for key in LEGAL_CLAUSE_KEYS:
        matches = [c for c in clauses if c.category == key]
        if matches:
            c = matches[0]
            f.clause_overview[key] = {
                "present": True, "count": len(matches), "summary": None, "source": "rule",
                "excerpt": c.text[:400], "page": c.page_number,
                "section": section_label(c.section_ref, c.heading or c.parent_heading),
            }
        else:
            f.clause_overview[key] = {"present": False, "count": 0, "summary": None, "source": "rule"}

    if any(fd.ambiguous for fd in find_dates(full_text[:20000])):
        f.warnings.append("Some numeric dates are ambiguous (day/month order); they were read as day/month/year")
    return f


# --- Obligations ----------------------------------------------------------------------------------------------

_OBLIGATION = re.compile(r"\b(shall|must|agrees?\s+to|is\s+required\s+to|undertakes?\s+to|is\s+responsible\s+for|will\s+be\s+responsible\s+for)\b", re.I)
_NEGATIVE_CONTEXT = re.compile(r"^(?:nothing|no\s+provision|this\s+agreement\s+shall\s+(?:be\s+governed|commence|remain))", re.I)


@dataclass
class ObligationItem:
    party: str | None
    description: str
    obligation_type: str
    due_description: str | None
    trigger_condition: str | None
    consequence: str | None
    evidence: Evidence


def _party_for(sentence: str, party_names: list[str]) -> str | None:
    modal = _OBLIGATION.search(sentence)
    subject = sentence[: modal.start()] if modal else sentence[:80]
    subject_l = subject.lower()
    if re.search(r"\b(each|either|both)\s+part(y|ies)\b", subject_l):
        return "Both parties"
    for name in party_names:
        if name and name.lower() in subject_l:
            return name
    for role in ROLE_WORDS:
        if re.search(rf"\b{re.escape(role.lower())}\b", subject_l):
            return role
    return None


def _obligation_type(sentence: str, category: str) -> str:
    s = sentence.lower()
    if re.search(r"\bpay|invoice|fee|reimburse", s):
        return "payment"
    if re.search(r"confidential|disclose", s):
        return "confidentiality"
    if re.search(r"deliver|provide|perform|complete|supply", s):
        return "delivery"
    if re.search(r"notif|notice|inform", s):
        return "notice"
    if re.search(r"report|record|audit", s):
        return "reporting"
    if re.search(r"comply|compliance|law|regulation|insurance", s):
        return "compliance"
    if category in ("data_protection",):
        return "data_protection"
    return "general"


def extract_obligations(clauses: list[Clause], parties: list[dict[str, Any]] | None, limit: int = 80) -> list[ObligationItem]:
    names = [p.get("role") or "" for p in (parties or [])] + [p.get("name") or "" for p in (parties or [])]
    items: list[ObligationItem] = []
    for c in clauses:
        if c.category in ("governing_law", "notices"):
            continue
        for sent in sentences(c.text):
            if len(sent) < 25 or not _OBLIGATION.search(sent) or _NEGATIVE_CONTEXT.search(sent):
                continue
            party = _party_for(sent, [n for n in names if n])
            if party is None:
                continue
            due = re.search(rf"(within\s+{_NUM_PAREN}\s*\w*\s*(?:day|week|month|year)s?[^,.;]*|no\s+later\s+than\s+[^,.;]+|prior\s+to\s+[^,.;]+|by\s+(?:{_MON}\s+\d{{1,2}},?\s+\d{{4}}|\d{{1,2}}\s+{_MON}\s+\d{{4}}))", sent, re.I)
            trig = re.search(r"\b(if|upon|in\s+the\s+event\s+(?:that|of)|where|following)\b\s+([^,;]{5,200})", sent, re.I)
            cons = re.search(r"(failing\s+which[^.;]*|otherwise[^.;]*|failure\s+to[^.;]*(?:shall|will)\s+(?:result|entitle|constitute)[^.;]*)", sent, re.I)
            items.append(ObligationItem(
                party=party, description=sent[:500], obligation_type=_obligation_type(sent, c.category),
                due_description=due.group(0)[:300] if due else None,
                trigger_condition=(trig.group(1) + " " + trig.group(2))[:300] if trig else None,
                consequence=cons.group(0)[:300] if cons else None, evidence=_ev(sent, c),
            ))
            if len(items) >= limit:
                return items
    return items


# --- Risk rules -----------------------------------------------------------------------------------------------

SEVERITY_ORDER = ["critical", "high", "medium", "low", "informational"]


@dataclass
class Finding:
    title: str
    description: str
    category: str
    severity: str
    explanation: str
    suggested_actions: list[str]
    evidence: Evidence | None
    rule_id: str | None = None
    source: str = "rule"
    verified: bool = True


RISK_CATEGORIES = {
    "financial": "Financial risk", "legal": "Legal risk", "operational": "Operational risk",
    "data_protection": "Data protection risk", "intellectual_property": "Intellectual property risk",
    "ambiguity": "Contractual ambiguity", "unusual": "Unusual or potentially unfavourable clause",
}


def _one_sided(sentence: str) -> bool:
    s = sentence.lower()
    return not re.search(r"\b(either|each|both)\s+part(y|ies)\b|\bmutual(ly)?\b|\bthe\s+parties\b", s)


def detect_risks(full_text: str, clauses: list[Clause], fields: ExtractedFields, today: date | None = None) -> list[Finding]:
    today = today or date.today()
    out: list[Finding] = []

    def add(rule_id: str, title: str, category: str, severity: str, description: str, explanation: str,
            actions: list[str], hit: tuple[str, Clause, Any] | None = None, evidence: Evidence | None = None) -> None:
        ev = _ev(hit[0], hit[1]) if hit else evidence
        out.append(Finding(title, description, category, severity, explanation, actions, ev, rule_id, "rule", ev is not None))

    present = {k for k, v in fields.clause_overview.items() if v.get("present")}
    text_l = full_text.lower()

    # Liability
    unlimited = find_sentences(clauses, re.compile(r"unlimited\s+liability|liability\s+(?:shall\s+)?(?:not\s+be\s+limited|is\s+unlimited)|without\s+(?:any\s+)?limit(?:ation)?\s+(?:of|on|as\s+to)\s+(?:its\s+|their\s+)?liability", re.I))
    if unlimited:
        add("LIAB-UNLIMITED", "Unlimited liability", "legal", "critical",
            "The contract appears to expose a party to liability without a cap.",
            "Uncapped liability means potential losses are not limited to the contract value. This is matched "
            "by a deterministic rule on wording such as 'unlimited liability'.",
            ["Confirm which party bears the unlimited liability", "Ask for a liability cap (e.g. fees paid in the last 12 months)",
             "Check whether insurance covers this exposure"], unlimited[0])
    elif "liability" not in present and not re.search(r"limitation\s+of\s+liability|aggregate\s+liability|shall\s+not\s+be\s+liable", text_l):
        add("LIAB-MISSING", "No limitation of liability clause found", "legal", "medium",
            "No clause limiting either party's liability was detected.",
            "Without a limitation clause, liability is generally determined by the governing law and may be broad. "
            "The rule looks for liability headings and common limitation wording; it may miss unusual phrasing.",
            ["Check whether liability is limited elsewhere (e.g. in referenced terms)", "Consider proposing a mutual liability cap"])

    # Indemnification
    indem = find_sentences(clauses, re.compile(r"\bindemnif(y|ies|ication)|hold\s+harmless", re.I))
    broad = [h for h in indem if re.search(r"any\s+and\s+all|all\s+(?:claims|losses|damages)|regardless|whether\s+or\s+not|howsoever", h[0], re.I)]
    if broad and _one_sided(broad[0][0]):
        add("INDEM-BROAD", "Broad one-sided indemnity", "legal", "high",
            "One party must indemnify the other for a broad range of claims.",
            "Wording such as 'any and all claims' combined with no reciprocal obligation can shift significant risk "
            "to one party. Detected by keyword rule; check who the indemnifying party is.",
            ["Ask for the indemnity to be mutual", "Limit the indemnity to claims caused by the indemnifying party's breach or negligence",
             "Check whether the indemnity is excluded from the liability cap"], broad[0])
    elif indem and _one_sided(indem[0][0]):
        add("INDEM-ONESIDED", "One-sided indemnification", "legal", "medium",
            "The indemnification obligation appears to apply to one party only.",
            "Detected by keyword rule: the indemnity sentence does not mention both parties.",
            ["Confirm which party gives the indemnity", "Consider requesting a mutual indemnity"], indem[0])

    # Renewal
    if fields.auto_renewal and fields.auto_renewal.value is True:
        notice = fields.notice_period_days.value if fields.notice_period_days else None
        sev = "high" if (notice and notice >= 60) else "medium"
        add("RENEW-AUTO", "Automatic renewal", "operational", sev,
            "The contract renews automatically unless notice is given." + (f" Notice period detected: {notice} days." if notice else ""),
            "Automatic renewal can lock parties into further terms if the notice window is missed. Severity is raised "
            "to high when the detected notice period is 60 days or longer.",
            ["Add the non-renewal notice deadline to the calendar", "Confirm how and to whom notice must be sent"],
            evidence=fields.auto_renewal.evidence)

    # Termination
    conv = find_sentences(clauses, re.compile(r"may\s+terminate\s+(?:this\s+agreement\s+)?(?:at\s+any\s+time|for\s+(?:any\s+reason|convenience|no\s+reason)|without\s+(?:cause|reason))", re.I))
    if conv and _one_sided(conv[0][0]):
        add("TERM-CONVENIENCE", "One-sided termination for convenience", "legal", "medium",
            "Only one party appears able to terminate without cause.",
            "A unilateral right to terminate at any time creates uncertainty for the other party. Detected by keyword rule.",
            ["Ask for a mutual termination right", "Request a minimum notice period and payment for work performed"], conv[0])
    if "termination" not in present and not re.search(r"\bterminat", text_l):
        add("TERM-MISSING", "No termination clause found", "legal", "medium",
            "No termination provisions were detected.",
            "Without termination rights it may be unclear how either party can exit the contract.",
            ["Check whether the contract ends only by expiry", "Consider adding termination for breach and insolvency"])

    # Payment
    for pd in fields.payment_deadlines:
        days = pd.get("days")
        if days and days > 45:
            add("PAY-LONG", f"Long payment period ({days} days)", "financial", "high" if days > 90 else "medium",
                f"Payment is due {days} days after the trigger event.",
                "Payment terms longer than 45 days can strain cash flow; over 90 days is rated high. Deterministic rule.",
                ["Consider negotiating shorter payment terms (e.g. 30 days)", "Ask for partial upfront payment or milestones"],
                evidence=Evidence(pd["quote"], pd.get("page"), pd.get("section")))
            break
    late_rate = find_sentences(clauses, re.compile(r"(?:interest|late\s+(?:fee|charge|payment)).{0,80}?(\d{1,2}(?:\.\d{1,2})?)\s?(?:%|percent|per\s+cent)\s*(?:per\s+|a\s+|each\s+)?(month|annum|year|day)?", re.I))
    for sent, clause, m in late_rate:
        rate = float(m.group(1))
        period = (m.group(2) or "").lower()
        annual = rate * 12 if period == "month" else rate * 365 if period == "day" else rate
        if annual >= 18:
            add("PAY-LATE-HIGH", "High late-payment interest", "financial", "high" if annual >= 24 else "medium",
                f"Late payments accrue interest of about {annual:.0f}% per year ({rate}% per {period or 'period'}).",
                "Rates of 18% per year or more are flagged; 24% or more is rated high. Calculated deterministically from the quoted rate.",
                ["Check which party pays the interest", "Compare the rate with statutory late-payment interest"], (sent, clause, m))
            break
    price = find_sentences(clauses, re.compile(r"may\s+(?:increase|change|adjust|revise)\s+(?:the\s+|its\s+|any\s+)?(?:fees|prices?|rates?|charges).{0,120}?(?:at\s+any\s+time|sole\s+discretion|without\s+(?:prior\s+)?notice|from\s+time\s+to\s+time)", re.I))
    if price:
        add("PRICE-UNILATERAL", "Unilateral price changes", "financial", "medium",
            "One party may change prices without the other's agreement.",
            "Unilateral price adjustment rights create cost uncertainty. Detected by keyword rule.",
            ["Ask for a cap on increases (e.g. CPI) and advance notice", "Request a right to terminate if prices increase"], price[0])
    liquidated = find_sentences(clauses, re.compile(r"liquidated\s+damages|penalty\s+of|contractual\s+penalty", re.I))
    if liquidated:
        add("PAY-LIQUIDATED", "Liquidated damages or penalties", "financial", "medium",
            "The contract specifies fixed damages or penalties.",
            "Pre-agreed damages may exceed actual losses. Detected by keyword rule.",
            ["Check the amount and trigger conditions", "Confirm whether penalties are capped"], liquidated[0])

    # Unilateral amendment
    amend = find_sentences(clauses, re.compile(r"may\s+(?:amend|modify|change|update)\s+(?:this\s+agreement|these\s+terms|the\s+terms|this\s+contract).{0,80}?(?:at\s+any\s+time|sole\s+discretion|without\s+(?:prior\s+)?notice|from\s+time\s+to\s+time)", re.I))
    if amend:
        add("AMEND-UNILATERAL", "Unilateral right to amend the contract", "unusual", "high",
            "One party can change the contract terms on its own.",
            "A right to amend the agreement unilaterally undermines the certainty of every other clause. Detected by keyword rule.",
            ["Require amendments to be in writing and signed by both parties", "Ask for notice and a right to terminate on changes"], amend[0])

    # Intellectual property
    ip_assign = find_sentences(clauses, re.compile(r"assign\w*.{0,120}?(?:all|any)\s+(?:of\s+its\s+)?(?:right|rights),?\s+title\s+and\s+interest|work\s+made\s+for\s+hire|shall\s+(?:vest|belong)\s+(?:exclusively\s+)?(?:in|to)", re.I))
    broad_ip = [h for h in ip_assign if re.search(r"pre-?existing|prior|whether\s+or\s+not|before\s+the\s+effective|background|any\s+and\s+all\s+intellectual|outside\s+(?:the\s+scope|working\s+hours)", h[0], re.I)]
    if broad_ip:
        add("IP-BROAD", "Broad intellectual property assignment", "intellectual_property", "high",
            "The IP assignment may extend beyond the deliverables, e.g. to pre-existing or unrelated work.",
            "Detected wording that assigns pre-existing, background or unrelated intellectual property.",
            ["Exclude pre-existing and background IP from the assignment", "Limit the assignment to deliverables paid for under the contract"], broad_ip[0])
    elif ip_assign:
        add("IP-ASSIGN", "Intellectual property is assigned", "intellectual_property", "low",
            "Ownership of intellectual property in the work transfers to one party.",
            "This is common in services contracts but should match the commercial intent. Detected by keyword rule.",
            ["Confirm the assignment only takes effect on full payment", "Check whether a licence back to the creator is needed"], ip_assign[0])
    if "intellectual_property" not in present and fields.contract_type and re.search(r"services|consult|contractor|statement|software|licen", fields.contract_type.value or "", re.I):
        add("IP-MISSING", "No intellectual property clause found", "intellectual_property", "medium",
            "The contract type usually addresses IP ownership, but no IP clause was detected.",
            "Without an IP clause, ownership of work product defaults to the governing law, which may not match expectations.",
            ["Clarify who owns deliverables and pre-existing materials"])

    # Non-compete / exclusivity
    nc = find_sentences(clauses, re.compile(r"non-?compet|shall\s+not\s+(?:directly\s+or\s+indirectly\s+)?(?:compete|engage\s+in\s+any\s+(?:business|activity)\s+(?:that|which)\s+compet)|competing\s+business", re.I))
    if nc:
        sent = nc[0][0]
        dur = re.search(rf"{_NUM_PAREN}\s*\(?\d*\)?\s*(year|month)s?", sent, re.I)
        months = None
        if dur and to_int(dur.group(1)):
            months = to_int(dur.group(1)) * (12 if dur.group(2).lower() == "year" else 1)  # type: ignore[operator]
        worldwide = bool(re.search(r"worldwide|anywhere\s+in\s+the\s+world|globally", sent, re.I))
        sev = "high" if (months and months >= 24) or worldwide else "medium"
        add("NONCOMPETE", "Non-compete restriction", "legal", sev,
            "A party is restricted from competing" + (f" for about {months} months" if months else "") + (" worldwide" if worldwide else "") + ".",
            "Non-compete restrictions can limit future work; rated high when 24 months or longer or worldwide. Enforceability depends on the governing law.",
            ["Check duration, territory and scope", "Ask a legal professional about enforceability in your jurisdiction"], nc[0])
    excl = [h for h in find_sentences(clauses, re.compile(r"\bexclusiv(e|ely|ity)\b", re.I)) if not re.search(r"jurisdiction|exclusive\s+of\s+(?:vat|tax)|non-exclusive|exclusive\s+remedy|exclusively\s+(?:governed|for\s+the\s+purpose)", h[0], re.I)]
    if excl:
        add("EXCLUSIVITY", "Exclusivity obligation", "operational", "medium",
            "The contract appears to include an exclusivity commitment.",
            "Exclusivity can prevent working with other customers or suppliers. Detected by keyword rule (excluding 'exclusive jurisdiction').",
            ["Confirm the scope, territory and duration of exclusivity", "Consider minimum commitments from the other party in return"], excl[0])

    # Confidentiality
    perpetual = find_sentences(clauses, re.compile(r"in\s+perpetuity|indefinitely|perpetual|survive\s+(?:indefinitely|without\s+limit)", re.I), {"confidentiality"})
    if perpetual:
        add("CONF-PERPETUAL", "Perpetual confidentiality obligation", "legal", "low",
            "Confidentiality obligations do not appear to expire.",
            "Perpetual confidentiality is common for trade secrets but can be burdensome for general information.",
            ["Consider a fixed duration (e.g. 3-5 years) except for trade secrets"], perpetual[0])
    if "confidentiality" not in present and not re.search(r"confidential", text_l):
        add("CONF-MISSING", "No confidentiality clause found", "legal", "low",
            "No confidentiality obligations were detected.",
            "Sensitive information shared under the contract may not be protected.",
            ["Consider adding mutual confidentiality obligations or a separate NDA"])

    # Data protection
    mentions_pd = re.search(r"personal\s+data|personal\s+information|customer\s+data|data\s+subjects?|\bpii\b", text_l)
    if mentions_pd and "data_protection" not in present and not re.search(r"gdpr|data\s+protection|data\s+processing\s+agreement|privacy\s+laws?", text_l):
        add("DP-MISSING", "Personal data without data-protection terms", "data_protection", "medium",
            "The contract mentions personal or customer data but no data-protection clause was detected.",
            "When personal data is processed, data-protection laws (e.g. GDPR) often require specific contract terms.",
            ["Check whether a data processing agreement is required", "Define security measures and breach notification duties"])
    transfer = find_sentences(clauses, re.compile(r"transfer\w*\s+(?:any\s+)?(?:personal\s+)?data.{0,80}?(?:outside|to\s+(?:any\s+)?(?:third\s+)?countr)", re.I))
    if transfer:
        add("DP-TRANSFER", "International data transfers", "data_protection", "medium",
            "Personal data may be transferred to other countries.",
            "Cross-border transfers can require additional safeguards under data-protection laws.",
            ["Confirm the transfer mechanism and destination countries"], transfer[0])

    # Disputes
    waiver = find_sentences(clauses, re.compile(r"waiv\w+.{0,60}?(?:jury\s+trial|class\s+action|right\s+to\s+(?:a\s+)?(?:jury|court))", re.I))
    if waiver:
        add("DISPUTE-WAIVER", "Waiver of jury trial or class action", "legal", "medium",
            "A party waives certain procedural rights in disputes.",
            "Such waivers limit dispute options. Detected by keyword rule.",
            ["Check whether the waiver is mutual and acceptable"], waiver[0])
    arb = find_sentences(clauses, re.compile(r"binding\s+arbitration|finally\s+(?:settled|resolved)\s+by\s+arbitration", re.I))
    if arb:
        add("DISPUTE-ARBITRATION", "Binding arbitration", "legal", "informational",
            "Disputes are resolved by arbitration rather than in court.",
            "Arbitration is common; consider the seat, rules and costs.",
            ["Check the arbitration seat, rules and who bears the costs"], arb[0])
    if fields.governing_law is None:
        add("GOVLAW-MISSING", "No governing law detected", "ambiguity", "low",
            "The contract does not appear to state which law governs it.",
            "Without a governing-law clause, determining the applicable law can be uncertain and costly.",
            ["Consider specifying the governing law and jurisdiction"])

    # Warranties / assignment
    as_is = find_sentences(clauses, re.compile(r"\"?as\s+is\"?|disclaims?\s+all\s+(?:other\s+)?warrant|without\s+warrant(?:y|ies)\s+of\s+any\s+kind", re.I))
    if as_is:
        add("WARRANTY-DISCLAIMER", "Broad warranty disclaimer", "legal", "low",
            "Warranties are disclaimed or the product/service is provided 'as is'.",
            "A broad disclaimer limits remedies if the deliverables are defective.",
            ["Ask for basic performance or conformity warranties"], as_is[0])
    assign = find_sentences(clauses, re.compile(r"may\s+assign.{0,80}?without\s+(?:the\s+)?(?:prior\s+)?(?:written\s+)?consent", re.I))
    if assign and _one_sided(assign[0][0]):
        add("ASSIGN-UNILATERAL", "One-sided assignment right", "legal", "low",
            "One party may transfer the contract without consent.",
            "The contract could end up with a counterparty you did not choose.",
            ["Ask for assignment to require consent (not unreasonably withheld)"], assign[0])

    # Ambiguity
    vague_pattern = re.compile(r"sole\s+(?:and\s+absolute\s+)?discretion|reasonable\s+efforts|best\s+efforts|commercially\s+reasonable|as\s+soon\s+as\s+(?:reasonably\s+)?practicable|from\s+time\s+to\s+time|to\s+be\s+agreed|tbd\b|\[.{0,20}\]", re.I)
    vague = find_sentences(clauses, vague_pattern)
    if len(vague) >= 3:
        add("AMBIG-VAGUE", f"Vague or discretionary wording ({len(vague)} instances)", "ambiguity", "low",
            "Several clauses rely on subjective standards such as 'sole discretion' or 'reasonable efforts', or contain placeholders.",
            "Vague standards are open to interpretation and can lead to disputes. Counted by a keyword rule.",
            ["Review each instance and define objective criteria where it matters"], vague[0])
    placeholder = find_sentences(clauses, re.compile(r"\[(?:insert|date|name|amount|tbd|●|x+)[^\]]{0,30}\]|_{5,}", re.I))
    if placeholder:
        add("AMBIG-PLACEHOLDER", "Unfilled placeholders", "ambiguity", "medium",
            "The document contains blanks or placeholder text.",
            "Unfilled placeholders suggest the contract is incomplete.",
            ["Complete all placeholders before signing"], placeholder[0])

    # Dates
    if fields.expiration_date and fields.expiration_date.value < today:
        out.append(Finding("Contract end date is in the past", f"The detected expiration date ({fields.expiration_date.value.isoformat()}) has passed.",
                           "operational", "informational", "Based on the extracted expiration date.",
                           ["Check whether the contract was renewed or replaced"], fields.expiration_date.evidence, "DATE-EXPIRED"))

    # Embedded instructions aimed at AI systems
    inj = find_sentences(clauses, re.compile(r"ignore\s+(?:all\s+|any\s+)?(?:previous|prior|above)\s+instructions|disregard\s+(?:all\s+|the\s+)?(?:previous|prior|above)|system\s+prompt|you\s+are\s+(?:an?\s+)?(?:ai|language\s+model|assistant)|rate\s+this\s+(?:contract|document)\s+as\s+(?:low|safe)", re.I))
    if inj:
        add("UNUSUAL-AI-INSTRUCTIONS", "Text addressed to AI systems", "unusual", "medium",
            "The document contains text that looks like instructions to an AI tool.",
            "Such text can be an attempt to manipulate automated review. ContractLens treats document text as data and ignores these instructions, but the clause deserves human attention.",
            ["Review the passage manually", "Ask the counterparty why the text is present"], inj[0])
    return out
