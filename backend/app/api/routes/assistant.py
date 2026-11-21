"""Document-grounded assistant conversations (per contract, per user)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select

from app.api.deps import DB, ViewerCtx, get_contract_or_404
from app.core.rate_limit import rate_limit
from app.models import ContractStatus, Conversation, ConversationMessage
from app.schemas import ConversationIn, ConversationOut, MessageIn, MessageOut
from app.services import assistant, audit

router = APIRouter(prefix="/workspaces/{workspace_id}/contracts/{contract_id}/conversations", tags=["assistant"])


def _conversation_or_404(db: DB, ctx: ViewerCtx, contract_id: uuid.UUID, conversation_id: uuid.UUID) -> Conversation:
    conv = db.scalar(select(Conversation).where(
        Conversation.id == conversation_id, Conversation.workspace_id == ctx.id,
        Conversation.contract_id == contract_id, Conversation.user_id == ctx.user.id))
    if conv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    return conv


@router.get("", response_model=list[ConversationOut])
def list_conversations(contract_id: uuid.UUID, ctx: ViewerCtx, db: DB) -> list[Conversation]:
    get_contract_or_404(db, ctx, contract_id)
    return list(db.scalars(select(Conversation).where(Conversation.workspace_id == ctx.id, Conversation.contract_id == contract_id,
                                                      Conversation.user_id == ctx.user.id).order_by(Conversation.updated_at.desc())))


@router.post("", response_model=ConversationOut, status_code=201)
def create_conversation(contract_id: uuid.UUID, body: ConversationIn, ctx: ViewerCtx, db: DB) -> Conversation:
    contract = get_contract_or_404(db, ctx, contract_id)
    conv = Conversation(workspace_id=ctx.id, contract_id=contract.id, user_id=ctx.user.id,
                        title=(body.title or f"Questions about {contract.title}")[:200])
    db.add(conv)
    db.commit()
    return conv


@router.get("/{conversation_id}/messages", response_model=list[MessageOut])
def list_messages(contract_id: uuid.UUID, conversation_id: uuid.UUID, ctx: ViewerCtx, db: DB) -> list[ConversationMessage]:
    conv = _conversation_or_404(db, ctx, contract_id, conversation_id)
    return list(db.scalars(select(ConversationMessage).where(ConversationMessage.conversation_id == conv.id,
                                                             ConversationMessage.workspace_id == ctx.id)
                           .order_by(ConversationMessage.created_at)))


@router.post("/{conversation_id}/messages", response_model=list[MessageOut], status_code=201,
             dependencies=[Depends(rate_limit("assistant", 30, 60))])
def ask(contract_id: uuid.UUID, conversation_id: uuid.UUID, body: MessageIn, ctx: ViewerCtx, db: DB) -> list[ConversationMessage]:
    contract = get_contract_or_404(db, ctx, contract_id)
    conv = _conversation_or_404(db, ctx, contract.id, conversation_id)
    if contract.status != ContractStatus.analyzed:
        raise HTTPException(status.HTTP_409_CONFLICT, "The contract must finish processing before questions can be answered")
    question = " ".join(body.content.split())
    user_msg = ConversationMessage(workspace_id=ctx.id, conversation_id=conv.id, role="user", content=question)
    db.add(user_msg)
    db.flush()
    reply = assistant.answer(db, ctx.workspace, contract, conv, question)
    conv.title = conv.title if db.scalar(select(ConversationMessage.id).where(
        ConversationMessage.conversation_id == conv.id, ConversationMessage.id != user_msg.id).limit(1)) else question[:200]
    audit.record(db, "assistant.question", user_id=ctx.user.id, workspace_id=ctx.id, target_type="contract",
                 target_id=contract.id, ip=ctx.ip, details={"mode": reply.mode})
    db.commit()
    return [user_msg, reply]


@router.delete("/{conversation_id}", status_code=204)
def delete_conversation(contract_id: uuid.UUID, conversation_id: uuid.UUID, ctx: ViewerCtx, db: DB) -> Response:
    conv = _conversation_or_404(db, ctx, contract_id, conversation_id)
    db.delete(conv)
    db.commit()
    return Response(status_code=204)
