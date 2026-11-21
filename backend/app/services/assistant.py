"""Document-grounded question answering.

Retrieval is limited to the selected contract within the caller's workspace. With an AI provider, the model may
only answer from the retrieved excerpts and every citation is verified against the excerpt text; an answer without
verifiable citations is withheld. Without a provider, the assistant returns the most relevant passages verbatim.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Contract, Conversation, ConversationMessage, Workspace
from app.services.ai.config import resolve_provider
from app.services.ai.prompts import QA_SYSTEM, frame_document
from app.services.ai.providers import AIError, call_structured
from app.services.ai.schemas import AIAnswer
from app.services.grounding import norm
from app.services.retrieval import Hit, best_sentence, search

log = logging.getLogger(__name__)

NOT_FOUND = ("I could not find this in the document. The passages searched do not address the question, so I "
             "cannot answer it without guessing.")


def _citation(hit: Hit, quote: str) -> dict[str, Any]:
    return {"chunk_id": hit.label, "quote": quote[:600], "page": hit.chunk.page_number, "section": hit.chunk.section_ref}


def _extractive(question: str, hits: list[Hit]) -> tuple[str, list[dict[str, Any]], bool | None]:
    top = [h for h in hits if h.score >= 1.0][:3]
    if not top:
        return NOT_FOUND, [], False
    content = ("AI answering is not configured, so this is not a generated answer. These are the passages of the "
               "document most relevant to your question - please read them to find the answer:")
    return content, [_citation(h, best_sentence(h.chunk.text, question)) for h in top], None


def _history(db: Session, conversation: Conversation, limit: int = 6) -> str:
    rows = list(db.scalars(select(ConversationMessage).where(ConversationMessage.conversation_id == conversation.id)
                           .order_by(ConversationMessage.created_at.desc()).limit(limit)))
    lines = [f"{m.role}: {m.content[:400]}" for m in reversed(rows)]
    return "\n".join(lines)


def answer(db: Session, workspace: Workspace, contract: Contract, conversation: Conversation, question: str) -> ConversationMessage:
    hits = search(db, workspace.id, contract.id, question, k=6)
    mode = "extractive"
    interpretation = None
    citations: list[dict[str, Any]] = []
    found: bool | None
    content: str

    provider = None
    try:
        provider = resolve_provider(workspace)
    except AIError:
        provider = None

    if not hits:
        content, found = NOT_FOUND, False
    elif provider is None:
        content, citations, found = _extractive(question, hits)
    else:
        mode = "ai"
        by_label = {h.label: h for h in hits}
        blocks = [(f"{h.label}" + (f" | page {h.chunk.page_number}" if h.chunk.page_number else "") +
                   (f" | section {h.chunk.section_ref}" if h.chunk.section_ref else ""), h.chunk.text) for h in hits]
        history = _history(db, conversation)
        user = (("Previous conversation (for context only; answer from the excerpts):\n" + history + "\n\n") if history else "") + \
               f"Excerpts from the contract:\n{frame_document(blocks)}\n\nQuestion: {question}"
        try:
            result = call_structured(provider, QA_SYSTEM, user, AIAnswer, max_tokens=1500)
            for c in result.citations:
                hit = by_label.get(c.chunk_id.strip().upper())
                if hit and len(norm(c.quote)) >= 8 and norm(c.quote) in norm(hit.chunk.text):
                    citations.append(_citation(hit, c.quote))
            if result.found and citations:
                content, found, interpretation = result.answer, True, result.interpretation
            elif result.found:
                content = ("The AI produced an answer, but none of its quotes could be verified against the document, "
                           "so it has been withheld. The most relevant passages are shown instead.")
                found = None
                citations = [_citation(h, best_sentence(h.chunk.text, question)) for h in hits[:3]]
            else:
                content, found = result.answer or NOT_FOUND, False
        except AIError as exc:
            log.warning("AI answer failed: %s", exc)
            content, citations, found = _extractive(question, hits)
            content = "The AI provider is currently unavailable. " + content
            mode = "extractive"

    msg = ConversationMessage(workspace_id=workspace.id, conversation_id=conversation.id, role="assistant",
                              content=content, interpretation=interpretation, citations=citations,
                              answer_found=found, mode=mode)
    db.add(msg)
    return msg
