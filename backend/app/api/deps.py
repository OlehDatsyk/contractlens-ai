"""Authentication and workspace authorisation dependencies.

Every workspace-scoped endpoint resolves the caller's membership first; resources are then loaded with BOTH the
resource id and the workspace id. Non-members receive 404 (not 403) so resource existence is never revealed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Path, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.rate_limit import client_ip
from app.core.security import decode_access_token
from app.models import ROLE_RANK, Contract, Role, User, Workspace, WorkspaceMembership

DB = Annotated[Session, Depends(get_db)]


def get_current_user(db: DB, authorization: Annotated[str | None, Header()] = None) -> User:
    unauthorized = HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated", headers={"WWW-Authenticate": "Bearer"})
    if not authorization or not authorization.lower().startswith("bearer "):
        raise unauthorized
    payload = decode_access_token(authorization[7:].strip())
    if payload is None:
        raise unauthorized
    try:
        user_id = uuid.UUID(str(payload["sub"]))
    except ValueError as exc:
        raise unauthorized from exc
    user = db.get(User, user_id)
    if user is None or not user.is_active or int(payload.get("ver", -1)) != user.token_version:
        raise unauthorized
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


@dataclass
class WorkspaceContext:
    workspace: Workspace
    membership: WorkspaceMembership
    user: User
    ip: str

    @property
    def id(self) -> uuid.UUID:
        return self.workspace.id

    @property
    def role(self) -> Role:
        return self.membership.role

    def can(self, minimum: Role) -> bool:
        return ROLE_RANK[self.role] >= ROLE_RANK[minimum]


def _workspace_context(minimum: Role):  # type: ignore[no-untyped-def]
    def dep(request: Request, db: DB, user: CurrentUser,
            workspace_id: Annotated[uuid.UUID, Path()]) -> WorkspaceContext:
        row = db.execute(
            select(Workspace, WorkspaceMembership)
            .join(WorkspaceMembership, WorkspaceMembership.workspace_id == Workspace.id)
            .where(Workspace.id == workspace_id, WorkspaceMembership.user_id == user.id)
        ).first()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Workspace not found")
        ws, membership = row
        if ROLE_RANK[membership.role] < ROLE_RANK[minimum]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"This action requires the {minimum.value} role or higher")
        return WorkspaceContext(ws, membership, user, client_ip(request))

    return dep


ViewerCtx = Annotated[WorkspaceContext, Depends(_workspace_context(Role.viewer))]
AnalystCtx = Annotated[WorkspaceContext, Depends(_workspace_context(Role.analyst))]
AdminCtx = Annotated[WorkspaceContext, Depends(_workspace_context(Role.admin))]
OwnerCtx = Annotated[WorkspaceContext, Depends(_workspace_context(Role.owner))]


def get_contract_or_404(db: Session, ctx: WorkspaceContext, contract_id: uuid.UUID) -> Contract:
    contract = db.scalar(select(Contract).where(Contract.id == contract_id, Contract.workspace_id == ctx.id))
    if contract is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contract not found")
    return contract


def require_csrf_header(x_requested_with: Annotated[str | None, Header()] = None) -> None:
    """Cookie-authenticated endpoints require a custom header, which cross-site forms cannot send."""
    if x_requested_with != "ContractLens":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing CSRF protection header")
