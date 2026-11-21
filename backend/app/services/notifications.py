"""In-app notifications and optional email delivery (only when SMTP is configured)."""

from __future__ import annotations

import logging
import smtplib
import ssl
import uuid
from email.message import EmailMessage

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Notification

log = logging.getLogger(__name__)


def notify(db: Session, user_id: uuid.UUID, workspace_id: uuid.UUID | None, kind: str, title: str, body: str = "",
           link: str | None = None) -> Notification:
    n = Notification(user_id=user_id, workspace_id=workspace_id, kind=kind, title=title[:200], body=body, link=link)
    db.add(n)
    return n


def send_email(to: str, subject: str, text: str) -> bool:
    """Send a plain-text email. Returns False (without raising) when email is not configured or fails."""
    s = get_settings()
    if not s.email_configured:
        return False
    msg = EmailMessage()
    msg["From"] = s.smtp_from
    msg["To"] = to
    msg["Subject"] = subject[:200]
    msg.set_content(text)
    try:
        if s.smtp_port == 465:
            with smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, context=ssl.create_default_context(), timeout=20) as smtp:
                if s.smtp_username:
                    smtp.login(s.smtp_username, s.smtp_password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=20) as smtp:
                if s.smtp_starttls:
                    smtp.starttls(context=ssl.create_default_context())
                if s.smtp_username:
                    smtp.login(s.smtp_username, s.smtp_password)
                smtp.send_message(msg)
        return True
    except (OSError, smtplib.SMTPException) as exc:
        log.warning("Email delivery failed: %s", type(exc).__name__)
        return False
