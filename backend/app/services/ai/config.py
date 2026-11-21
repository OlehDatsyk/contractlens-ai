"""Resolves which AI provider (if any) a workspace uses."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import get_settings
from app.core.security import decrypt_secret
from app.models import Workspace
from app.services.ai.providers import PROVIDERS, AIProvider, make_provider


@dataclass
class AIStatus:
    available: bool
    provider: str | None
    model: str | None
    source: str | None  # "workspace" | "server"
    reason: str | None

    def as_dict(self) -> dict[str, object]:
        return {"available": self.available, "provider": self.provider, "model": self.model,
                "source": self.source, "reason": self.reason}


def ai_status(workspace: Workspace | None) -> AIStatus:
    s = get_settings()
    if workspace is not None and not workspace.ai_enabled:
        return AIStatus(False, None, None, None, "AI features are turned off for this workspace")
    if workspace is not None and workspace.ai_provider in PROVIDERS and workspace.ai_api_key_encrypted:
        if decrypt_secret(workspace.ai_api_key_encrypted) is None:
            return AIStatus(False, workspace.ai_provider, None, "workspace",
                            "The stored workspace API key cannot be decrypted (was ENCRYPTION_KEY changed?)")
        model = workspace.ai_model or s.provider_model(workspace.ai_provider)
        return AIStatus(True, workspace.ai_provider, model, "workspace", None)
    if s.ai_provider in PROVIDERS and s.provider_key(s.ai_provider):
        model = (workspace.ai_model if workspace and workspace.ai_provider == s.ai_provider and workspace.ai_model
                 else s.provider_model(s.ai_provider))
        return AIStatus(True, s.ai_provider, model, "server", None)
    if s.ai_provider in PROVIDERS:
        return AIStatus(False, s.ai_provider, None, "server",
                        f"AI_PROVIDER is '{s.ai_provider}' but no API key is configured")
    return AIStatus(False, None, None, None, "No AI provider is configured.")


def resolve_provider(workspace: Workspace | None) -> AIProvider | None:
    status = ai_status(workspace)
    if not status.available or not status.provider or not status.model:
        return None
    s = get_settings()
    if status.source == "workspace" and workspace and workspace.ai_api_key_encrypted:
        key = decrypt_secret(workspace.ai_api_key_encrypted) or ""
    else:
        key = s.provider_key(status.provider)
    return make_provider(status.provider, key, status.model)
