"""Workspaces, membership/RBAC, invitations, folders, settings and the audit log."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Body, HTTPException, Query, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import DB, AdminCtx, AnalystCtx, CurrentUser, OwnerCtx, ViewerCtx
from app.core.config import get_settings
from app.core.rate_limit import client_ip
from app.core.security import decrypt_secret, encrypt_secret, hash_token, mask_secret, new_opaque_token
from app.models import (
    ROLE_RANK,
    AuditEvent,
    Contract,
    Folder,
    Role,
    User,
    Workspace,
    WorkspaceInvitation,
    WorkspaceMembership,
)
from app.schemas import (
    AISettingsOut,
    AuditOut,
    FolderIn,
    FolderOut,
    InvitationIn,
    InvitationOut,
    MemberOut,
    MemberUpdate,
    WorkspaceIn,
    WorkspaceOut,
    WorkspaceSettingsOut,
    WorkspaceSettingsUpdate,
)
from app.services import audit, notifications
from app.services.ai.config import ai_status
from app.services.contracts import delete_blobs, storage_keys_for

router = APIRouter(tags=["workspaces"])
MAX_WORKSPACES_PER_USER = 20


def _ws_out(db: DB, ws: Workspace, role: Role) -> WorkspaceOut:
    count = db.scalar(select(func.count()).select_from(WorkspaceMembership).where(WorkspaceMembership.workspace_id == ws.id)) or 0
    return WorkspaceOut(id=ws.id, name=ws.name, role=role, created_at=ws.created_at, member_count=count)


@router.get("/workspaces", response_model=list[WorkspaceOut])
def list_workspaces(user: CurrentUser, db: DB) -> list[WorkspaceOut]:
    counts = (select(WorkspaceMembership.workspace_id, func.count().label("n"))
              .group_by(WorkspaceMembership.workspace_id).subquery())
    rows = db.execute(
        select(Workspace, WorkspaceMembership.role, counts.c.n)
        .join(WorkspaceMembership, WorkspaceMembership.workspace_id == Workspace.id)
        .join(counts, counts.c.workspace_id == Workspace.id)
        .where(WorkspaceMembership.user_id == user.id).order_by(Workspace.created_at)
    ).all()
    return [WorkspaceOut(id=ws.id, name=ws.name, role=role, created_at=ws.created_at, member_count=n) for ws, role, n in rows]


@router.post("/workspaces", response_model=WorkspaceOut, status_code=201)
def create_workspace(body: WorkspaceIn, user: CurrentUser, request: Request, db: DB) -> WorkspaceOut:
    owned = db.scalar(select(func.count()).select_from(WorkspaceMembership)
                      .where(WorkspaceMembership.user_id == user.id, WorkspaceMembership.role == Role.owner)) or 0
    if owned >= MAX_WORKSPACES_PER_USER:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"You can own at most {MAX_WORKSPACES_PER_USER} workspaces")
    ws = Workspace(name=body.name, created_by=user.id)
    db.add(ws)
    db.flush()
    db.add(WorkspaceMembership(workspace_id=ws.id, user_id=user.id, role=Role.owner))
    audit.record(db, "workspace.create", user_id=user.id, workspace_id=ws.id, ip=client_ip(request))
    db.commit()
    return _ws_out(db, ws, Role.owner)


@router.get("/workspaces/{workspace_id}", response_model=WorkspaceOut)
def get_workspace(ctx: ViewerCtx, db: DB) -> WorkspaceOut:
    return _ws_out(db, ctx.workspace, ctx.role)


def _settings_out(ctx: ViewerCtx | AdminCtx) -> WorkspaceSettingsOut:
    ws = ctx.workspace
    st = ai_status(ws)
    hint = None
    if ws.ai_api_key_encrypted and ctx.can(Role.admin):
        plain = decrypt_secret(ws.ai_api_key_encrypted)
        hint = mask_secret(plain) if plain else "unreadable"
    s = get_settings()
    return WorkspaceSettingsOut(
        id=ws.id, name=ws.name, retention_days=ws.retention_days,
        ai=AISettingsOut(available=st.available, provider=st.provider, model=st.model, source=st.source, reason=st.reason,
                         ai_enabled=ws.ai_enabled, workspace_provider=ws.ai_provider, workspace_model=ws.ai_model,
                         workspace_key_set=bool(ws.ai_api_key_encrypted), workspace_key_hint=hint,
                         server_provider=s.ai_provider if s.ai_provider != "none" else None),
    )


@router.get("/workspaces/{workspace_id}/settings", response_model=WorkspaceSettingsOut)
def get_settings_route(ctx: ViewerCtx) -> WorkspaceSettingsOut:
    return _settings_out(ctx)


@router.patch("/workspaces/{workspace_id}/settings", response_model=WorkspaceSettingsOut)
def update_settings(body: WorkspaceSettingsUpdate, ctx: AdminCtx, db: DB) -> WorkspaceSettingsOut:
    ws = ctx.workspace
    changes: dict[str, object] = {}
    if body.name is not None:
        ws.name = " ".join(body.name.split())
        changes["name"] = True
    if body.clear_retention:
        ws.retention_days = None
        changes["retention_days"] = None
    elif body.retention_days is not None:
        ws.retention_days = body.retention_days
        changes["retention_days"] = body.retention_days
    ai_change = body.ai_provider is not None or body.ai_api_key is not None or body.clear_ai_key or body.ai_model is not None or body.ai_enabled is not None
    if ai_change and not ctx.can(Role.owner):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only workspace owners can change AI settings")
    if body.ai_enabled is not None:
        ws.ai_enabled = body.ai_enabled
        changes["ai_enabled"] = body.ai_enabled
    if body.ai_provider == "server":
        ws.ai_provider = None
        ws.ai_api_key_encrypted = None
        changes["ai_provider"] = "server"
    elif body.ai_provider is not None:
        if body.ai_provider != ws.ai_provider and not body.ai_api_key:
            ws.ai_api_key_encrypted = None
        ws.ai_provider = body.ai_provider
        changes["ai_provider"] = body.ai_provider
    if body.ai_model is not None:
        ws.ai_model = body.ai_model.strip() or None
        changes["ai_model"] = ws.ai_model
    if body.clear_ai_key:
        ws.ai_api_key_encrypted = None
        changes["ai_key"] = "cleared"
    elif body.ai_api_key:
        if not ws.ai_provider:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose an AI provider before saving an API key")
        ws.ai_api_key_encrypted = encrypt_secret(body.ai_api_key.strip())
        changes["ai_key"] = "updated"
    audit.record(db, "workspace.settings_update", user_id=ctx.user.id, workspace_id=ws.id, ip=ctx.ip, details=changes)
    db.commit()
    return _settings_out(ctx)


@router.delete("/workspaces/{workspace_id}", status_code=204)
def delete_workspace(ctx: OwnerCtx, db: DB, confirm_name: str = Body(embed=True)) -> Response:
    ws = ctx.workspace
    if confirm_name.strip() != ws.name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Type the workspace name exactly to confirm deletion")
    keys: list[str] = []
    for contract in db.scalars(select(Contract).where(Contract.workspace_id == ws.id)).all():
        keys += storage_keys_for(db, contract)
    audit.record(db, "workspace.delete", user_id=ctx.user.id, workspace_id=None, ip=ctx.ip,
                 details={"workspace_id": str(ws.id), "files": len(keys)})
    db.delete(ws)
    db.commit()
    delete_blobs(db, keys)
    db.commit()
    return Response(status_code=204)


# --- Members ------------------------------------------------------------------------------------------------

@router.get("/workspaces/{workspace_id}/members", response_model=list[MemberOut])
def list_members(ctx: ViewerCtx, db: DB) -> list[MemberOut]:
    rows = db.execute(select(WorkspaceMembership, User).join(User, User.id == WorkspaceMembership.user_id)
                      .where(WorkspaceMembership.workspace_id == ctx.id).order_by(WorkspaceMembership.created_at)).all()
    return [MemberOut(user_id=u.id, email=u.email, full_name=u.full_name, role=m.role, joined_at=m.created_at) for m, u in rows]


def _owner_count(db: DB, workspace_id: uuid.UUID) -> int:
    return db.scalar(select(func.count()).select_from(WorkspaceMembership)
                     .where(WorkspaceMembership.workspace_id == workspace_id, WorkspaceMembership.role == Role.owner)) or 0


def _target_membership(db: DB, ctx: AdminCtx, user_id: uuid.UUID) -> WorkspaceMembership:
    m = db.scalar(select(WorkspaceMembership).where(WorkspaceMembership.workspace_id == ctx.id, WorkspaceMembership.user_id == user_id))
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Member not found")
    return m


@router.patch("/workspaces/{workspace_id}/members/{user_id}", response_model=MemberOut)
def update_member(user_id: uuid.UUID, body: MemberUpdate, ctx: AdminCtx, db: DB) -> MemberOut:
    target = _target_membership(db, ctx, user_id)
    if not ctx.can(Role.owner):
        # Administrators manage analysts and viewers only.
        if ROLE_RANK[target.role] >= ROLE_RANK[Role.admin] or ROLE_RANK[body.role] >= ROLE_RANK[Role.admin]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Only owners can change administrator or owner roles")
    if target.role == Role.owner and body.role != Role.owner and _owner_count(db, ctx.id) <= 1:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A workspace must keep at least one owner")
    old = target.role
    target.role = body.role
    audit.record(db, "member.role_change", user_id=ctx.user.id, workspace_id=ctx.id, target_type="user", target_id=user_id,
                 ip=ctx.ip, details={"from": old.value, "to": body.role.value})
    user = db.get(User, user_id)
    assert user is not None
    notifications.notify(db, user_id, ctx.id, "role_changed", f"Your role in {ctx.workspace.name} is now {body.role.value}")
    db.commit()
    return MemberOut(user_id=user.id, email=user.email, full_name=user.full_name, role=target.role, joined_at=target.created_at)


@router.delete("/workspaces/{workspace_id}/members/{user_id}", status_code=204)
def remove_member(user_id: uuid.UUID, ctx: ViewerCtx, db: DB) -> Response:
    target = db.scalar(select(WorkspaceMembership).where(WorkspaceMembership.workspace_id == ctx.id, WorkspaceMembership.user_id == user_id))
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Member not found")
    leaving_self = user_id == ctx.user.id
    if not leaving_self:
        if not ctx.can(Role.admin):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Only administrators can remove members")
        if ROLE_RANK[target.role] >= ROLE_RANK[Role.admin] and not ctx.can(Role.owner):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Only owners can remove administrators or owners")
    if target.role == Role.owner and _owner_count(db, ctx.id) <= 1:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The last owner cannot leave; transfer ownership or delete the workspace")
    db.delete(target)
    audit.record(db, "member.leave" if leaving_self else "member.remove", user_id=ctx.user.id, workspace_id=ctx.id,
                 target_type="user", target_id=user_id, ip=ctx.ip)
    db.commit()
    return Response(status_code=204)


# --- Invitations --------------------------------------------------------------------------------------------

@router.get("/workspaces/{workspace_id}/invitations", response_model=list[InvitationOut])
def list_invitations(ctx: AdminCtx, db: DB) -> list[WorkspaceInvitation]:
    return list(db.scalars(select(WorkspaceInvitation).where(WorkspaceInvitation.workspace_id == ctx.id,
                                                             WorkspaceInvitation.accepted_at.is_(None))
                           .order_by(WorkspaceInvitation.created_at.desc())))


@router.post("/workspaces/{workspace_id}/invitations", status_code=201)
def create_invitation(body: InvitationIn, ctx: AdminCtx, db: DB) -> dict[str, object]:
    email = body.email.lower()
    if body.role == Role.owner and not ctx.can(Role.owner):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only owners can invite owners")
    if body.role == Role.admin and not ctx.can(Role.owner):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only owners can invite administrators")
    existing_member = db.scalar(select(WorkspaceMembership).join(User, User.id == WorkspaceMembership.user_id)
                                .where(WorkspaceMembership.workspace_id == ctx.id, User.email == email))
    if existing_member:
        raise HTTPException(status.HTTP_409_CONFLICT, "This person is already a member")
    pending = db.scalar(select(func.count()).select_from(WorkspaceInvitation)
                        .where(WorkspaceInvitation.workspace_id == ctx.id, WorkspaceInvitation.accepted_at.is_(None))) or 0
    if pending >= 100:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Too many pending invitations")
    raw = new_opaque_token()
    inv = WorkspaceInvitation(workspace_id=ctx.id, email=email, role=body.role, token_hash=hash_token(raw),
                              invited_by=ctx.user.id, expires_at=datetime.now(UTC) + timedelta(days=7))
    db.add(inv)
    audit.record(db, "invitation.create", user_id=ctx.user.id, workspace_id=ctx.id, target_type="invitation",
                 ip=ctx.ip, details={"role": body.role.value})
    db.flush()
    link = f"{get_settings().frontend_url.rstrip('/')}/invite?token={raw}"
    emailed = notifications.send_email(
        email, f"You're invited to {ctx.workspace.name} on ContractLens AI",
        f"{ctx.user.full_name} invited you to join the workspace '{ctx.workspace.name}' as {body.role.value}.\n\n"
        f"Accept the invitation (valid for 7 days, sign in with this email address):\n{link}\n")
    invitee = db.scalar(select(User).where(User.email == email))
    if invitee:
        notifications.notify(db, invitee.id, None, "invitation", f"Invitation to {ctx.workspace.name}",
                             "Open the invitation link you received to join.", None)
    db.commit()
    # The link is returned once so it can be shared securely when email is not configured. It is not stored.
    return {"invitation": InvitationOut.model_validate(inv).model_dump(mode="json"), "invite_link": link, "emailed": emailed}


@router.delete("/workspaces/{workspace_id}/invitations/{invitation_id}", status_code=204)
def revoke_invitation(invitation_id: uuid.UUID, ctx: AdminCtx, db: DB) -> Response:
    inv = db.scalar(select(WorkspaceInvitation).where(WorkspaceInvitation.id == invitation_id, WorkspaceInvitation.workspace_id == ctx.id))
    if inv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invitation not found")
    db.delete(inv)
    audit.record(db, "invitation.revoke", user_id=ctx.user.id, workspace_id=ctx.id, target_type="invitation",
                 target_id=invitation_id, ip=ctx.ip)
    db.commit()
    return Response(status_code=204)


@router.post("/invitations/accept", response_model=WorkspaceOut)
def accept_invitation(user: CurrentUser, request: Request, db: DB, token: str = Body(embed=True, min_length=20, max_length=200)) -> WorkspaceOut:
    inv = db.scalar(select(WorkspaceInvitation).where(WorkspaceInvitation.token_hash == hash_token(token)).with_for_update())
    if inv is None or inv.accepted_at is not None or inv.expires_at < datetime.now(UTC):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This invitation is invalid or has expired")
    if inv.email != user.email:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This invitation was sent to a different email address")
    ws = db.get(Workspace, inv.workspace_id)
    assert ws is not None
    existing = db.scalar(select(WorkspaceMembership).where(WorkspaceMembership.workspace_id == ws.id, WorkspaceMembership.user_id == user.id))
    if existing is None:
        db.add(WorkspaceMembership(workspace_id=ws.id, user_id=user.id, role=inv.role))
    inv.accepted_at = datetime.now(UTC)
    audit.record(db, "invitation.accept", user_id=user.id, workspace_id=ws.id, target_type="invitation", target_id=inv.id,
                 ip=client_ip(request))
    db.commit()
    return _ws_out(db, ws, existing.role if existing else inv.role)


# --- Folders ------------------------------------------------------------------------------------------------

@router.get("/workspaces/{workspace_id}/folders", response_model=list[FolderOut])
def list_folders(ctx: ViewerCtx, db: DB) -> list[FolderOut]:
    rows = db.execute(select(Folder, func.count(Contract.id)).outerjoin(Contract, Contract.folder_id == Folder.id)
                      .where(Folder.workspace_id == ctx.id).group_by(Folder.id).order_by(Folder.name)).all()
    return [FolderOut(id=f.id, name=f.name, contract_count=n) for f, n in rows]


@router.post("/workspaces/{workspace_id}/folders", response_model=FolderOut, status_code=201)
def create_folder(body: FolderIn, ctx: AnalystCtx, db: DB) -> FolderOut:
    folder = Folder(workspace_id=ctx.id, name=body.name)
    db.add(folder)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "A folder with this name already exists") from exc
    return FolderOut(id=folder.id, name=folder.name, contract_count=0)


@router.patch("/workspaces/{workspace_id}/folders/{folder_id}", response_model=FolderOut)
def rename_folder(folder_id: uuid.UUID, body: FolderIn, ctx: AnalystCtx, db: DB) -> FolderOut:
    folder = db.scalar(select(Folder).where(Folder.id == folder_id, Folder.workspace_id == ctx.id))
    if folder is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Folder not found")
    folder.name = body.name
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "A folder with this name already exists") from exc
    n = db.scalar(select(func.count()).select_from(Contract).where(Contract.folder_id == folder.id)) or 0
    return FolderOut(id=folder.id, name=folder.name, contract_count=n)


@router.delete("/workspaces/{workspace_id}/folders/{folder_id}", status_code=204)
def delete_folder(folder_id: uuid.UUID, ctx: AnalystCtx, db: DB) -> Response:
    folder = db.scalar(select(Folder).where(Folder.id == folder_id, Folder.workspace_id == ctx.id))
    if folder is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Folder not found")
    db.delete(folder)  # contracts are kept; their folder_id is set to NULL
    db.commit()
    return Response(status_code=204)


# --- Audit log ----------------------------------------------------------------------------------------------

@router.get("/workspaces/{workspace_id}/audit", response_model=list[AuditOut])
def audit_log(ctx: AdminCtx, db: DB, limit: int = Query(100, ge=1, le=500), before: datetime | None = None) -> list[AuditOut]:
    q = (select(AuditEvent, User.email).outerjoin(User, User.id == AuditEvent.user_id)
         .where(AuditEvent.workspace_id == ctx.id))
    if before:
        q = q.where(AuditEvent.created_at < before)
    rows = db.execute(q.order_by(AuditEvent.created_at.desc()).limit(limit)).all()
    out = []
    for ev, email in rows:
        item = AuditOut.model_validate(ev)
        item.user_email = email
        out.append(item)
    return out
