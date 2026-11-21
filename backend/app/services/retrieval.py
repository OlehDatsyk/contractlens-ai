"""Lexical retrieval (BM25) over one contract's chunks. Always scoped by workspace AND contract id."""

from __future__ import annotations

import math
import re
import uuid
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DocumentChunk

_STOP = {"a", "an", "and", "are", "as", "at", "be", "by", "can", "do", "does", "for", "from", "has", "have", "how", "i", "if", "in", "is", "it", "its", "me", "my", "of", "on", "or", "our", "shall", "should", "that", "the", "their", "there", "this", "to", "under", "us", "was", "we", "what", "when", "where", "which", "who", "will", "with", "would", "you", "your", "any", "whether", "about", "than", "then", "them", "they", "these", "those", "into", "over", "such", "also", "may", "must", "not", "no"}

_SYNONYMS = {
    "expire": ["expiration", "expiry", "term", "end", "until"], "expires": ["expiration", "expiry", "term", "end"],
    "terminate": ["termination", "cancel"], "early": ["convenience", "notice", "terminate"],
    "late": ["overdue", "interest", "delay", "penalty"], "pay": ["payment", "invoice", "fee", "fees"],
    "payment": ["invoice", "fee", "pay", "payable"], "own": ["ownership", "intellectual", "property", "assign", "title"],
    "ip": ["intellectual", "property", "copyright"], "renew": ["renewal", "automatically", "extend"],
    "renewal": ["renew", "automatically", "successive"], "obligations": ["shall", "must", "responsible"],
    "confidential": ["confidentiality", "disclose"], "liable": ["liability", "damages"], "liability": ["liable", "damages", "cap"],
    "law": ["governed", "governing", "jurisdiction"], "data": ["personal", "gdpr", "privacy"],
    "review": ["liability", "indemnify", "terminate", "exclusive", "renew"], "risky": ["liability", "indemnify", "penalty"],
}


def tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in _STOP and len(t) > 1]


def _stem(t: str) -> str:
    for suf in ("ation", "ing", "ies", "ed", "es", "s"):
        if len(t) > len(suf) + 3 and t.endswith(suf):
            return t[: -len(suf)]
    return t


@dataclass
class Hit:
    chunk: DocumentChunk
    score: float
    label: str


def search(db: Session, workspace_id: uuid.UUID, contract_id: uuid.UUID, query: str, k: int = 6) -> list[Hit]:
    chunks = list(db.scalars(
        select(DocumentChunk)
        .where(DocumentChunk.workspace_id == workspace_id, DocumentChunk.contract_id == contract_id)
        .order_by(DocumentChunk.chunk_index)
    ))
    if not chunks:
        return []
    q = [_stem(t) for t in tokens(query)]
    expanded = list(q)
    for t in tokens(query):
        expanded += [_stem(x) for x in _SYNONYMS.get(t, [])]
    docs = [[_stem(t) for t in tokens(c.text + " " + (c.section_ref or ""))] for c in chunks]
    n = len(docs)
    avg = sum(len(d) for d in docs) / max(n, 1)
    df: Counter[str] = Counter()
    for d in docs:
        df.update(set(d))
    k1, b = 1.5, 0.75
    hits: list[Hit] = []
    for chunk, d in zip(chunks, docs, strict=True):
        tf = Counter(d)
        score = 0.0
        for term in set(expanded):
            if term not in tf:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            weight = 1.0 if term in q else 0.4
            score += weight * idf * tf[term] * (k1 + 1) / (tf[term] + k1 * (1 - b + b * len(d) / max(avg, 1)))
        if score > 0:
            hits.append(Hit(chunk, score, f"C{chunk.chunk_index + 1}"))
    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:k]


def best_sentence(text: str, query: str) -> str:
    q = {_stem(t) for t in tokens(query)}
    best, best_score = text[:300], -1
    for sent in re.split(r"(?<=[.;])\s+", text.replace("\n", " ")):
        score = len(q & {_stem(t) for t in tokens(sent)})
        if score > best_score and len(sent) > 20:
            best, best_score = sent, score
    return best[:500]
