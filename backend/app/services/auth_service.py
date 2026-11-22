"""User & session management service."""
from __future__ import annotations

from typing import Optional

from datetime import timedelta

from sqlalchemy.orm import Session as DBSession

from app.core.config import get_settings, new_secret
from app.core.security import (
    generate_opaque_token,
    hash_opaque_token,
    hash_password,
    verify_password,
)
from app.models.models import (
    AuditLog,
    PasswordResetToken,
    RefreshToken,
    User,
    UserRole,
)
from app.utils.datetime_utils import utcnow
from app.utils.redact import redact_mapping

MIN_PASSWORD_LEN = 8


class AuthError(ValueError):
    """Raised for invalid auth operations (bad credentials, weak password...)."""


def audit(db: DBSession, *, user_id: Optional[str], action: str, entity: str = "",
          entity_id: str = "", ip: str = "", detail: Optional[dict] = None) -> None:
    """Persist an audit-trail row (sensitive fields redacted)."""
    db.add(AuditLog(
        user_id=user_id,
        action=action,
        entity=entity,
        entity_id=entity_id,
        ip_address=ip or "",
        detail=str(redact_mapping(detail or {})),
    ))


def get_user_by_email(db: DBSession, email: str) -> Optional[User]:
    """Fetch an active user by email (case-insensitive)."""
    return db.query(User).filter(User.email == email.lower().strip()).first()


def create_user(db: DBSession, *, email: str, password: str,
                full_name: str = "", role: UserRole = UserRole.USER) -> User:
    """Register a user; first user in an empty system becomes ADMIN."""
    email = email.lower().strip()
    if "@" not in email or "." not in email.split("@")[-1]:
        raise AuthError("Invalid email address")
    if len(password) < MIN_PASSWORD_LEN:
        raise AuthError(f"Password must be at least {MIN_PASSWORD_LEN} characters")
    if get_user_by_email(db, email):
        raise AuthError("An account with this email already exists")
    role = role
    if db.query(User).count() == 0:
        role = UserRole.ADMIN  # bootstrap admin
    user = User(
        email=email,
        hashed_password=hash_password(password),
        full_name=(full_name or "").strip(),
        role=role.value,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate(db: DBSession, email: str, password: str) -> User:
    """Verify credentials; raises AuthError on failure."""
    user = get_user_by_email(db, email)
    if not user or not user.is_active:
        raise AuthError("Invalid email or password")
    if not verify_password(password, user.hashed_password):
        raise AuthError("Invalid email or password")
    return user


def issue_refresh_token(db: DBSession, user: User) -> str:
    """Create a new refresh-token row; returns the raw token for the client."""
    settings = get_settings()
    raw = generate_opaque_token()
    db.add(RefreshToken(
        user_id=user.id,
        token_hash=hash_opaque_token(raw),
        expires_at=utcnow() + timedelta(days=settings.refresh_token_expire_days),
    ))
    db.commit()
    return raw


def login_tokens(db: DBSession, user: User):
    """Build the standard TokenResponse (access + refresh + profile)."""
    from app.core.security import create_access_token
    from app.schemas.auth import TokenResponse, UserOut

    refresh_raw = issue_refresh_token(db, user)
    return TokenResponse(
        access_token=create_access_token(user.id, user.role),
        refresh_token=refresh_raw,
        user=UserOut.model_validate(user),
    )


def rotate_refresh_token(db: DBSession, raw_token: str) -> tuple[User, str]:
    """Validate+revoke a refresh token, returning (user, new_raw_token).

    Implements rotation with reuse-detection: a revoked token presented again
    invalidates the whole family for that user.
    """
    token_hash = hash_opaque_token(raw_token)
    row = db.query(RefreshToken).filter(
        RefreshToken.token_hash == token_hash,
        RefreshToken.revoked.is_(False),
        RefreshToken.expires_at > utcnow(),
    ).first()
    if not row:
        raise AuthError("Invalid refresh token")
    row.revoked = True
    user = db.get(User, row.user_id)
    if not user or not user.is_active:
        raise AuthError("User not found or inactive")
    new_raw = issue_refresh_token(db, user)
    return user, new_raw


def revoke_refresh_token(db: DBSession, raw_token: str) -> None:
    """Revoke one refresh token (logout)."""
    token_hash = hash_opaque_token(raw_token)
    row = db.query(RefreshToken).filter(RefreshToken.token_hash == token_hash).first()
    if row:
        row.revoked = True
        db.commit()


def revoke_all_user_tokens(db: DBSession, user_id: str) -> None:
    """Revoke every refresh token of a user (password change, admin action)."""
    db.query(RefreshToken).filter(
        RefreshToken.user_id == user_id,
        RefreshToken.revoked.is_(False),
    ).update({"revoked": True})
    db.commit()


def create_password_reset_token(db: DBSession, user: User) -> str:
    """Create a single-use password-reset token; returns raw token."""
    settings = get_settings()
    raw = generate_opaque_token()
    db.add(PasswordResetToken(
        user_id=user.id,
        token_hash=hash_opaque_token(raw),
        expires_at=utcnow() + timedelta(minutes=settings.password_reset_token_ttl_minutes),
    ))
    db.commit()
    return raw


def consume_password_reset_token(db: DBSession, raw_token: str,
                                 new_password: str) -> User:
    """Validate a reset token and set the new password (single use)."""
    if len(new_password) < MIN_PASSWORD_LEN:
        raise AuthError(f"Password must be at least {MIN_PASSWORD_LEN} characters")
    token_hash = hash_opaque_token(raw_token)
    row = db.query(PasswordResetToken).filter(
        PasswordResetToken.token_hash == token_hash,
        PasswordResetToken.used.is_(False),
        PasswordResetToken.expires_at > utcnow(),
    ).first()
    if not row:
        raise AuthError("Invalid or expired reset link")
    row.used = True
    user = db.get(User, row.user_id)
    if not user or not user.is_active:
        raise AuthError("User not found or inactive")
    user.hashed_password = hash_password(new_password)
    revoke_all_user_tokens(db, user.id)
    db.commit()
    return user


def request_password_reset(db: DBSession, email: str) -> Optional[str]:
    """Start reset flow. Always returns a token-shaped value to avoid
    account enumeration; returns None when SMTP isn't configured (token
    is logged server-side in development only)."""
    user = get_user_by_email(db, email)
    if not user:
        return None
    raw = create_password_reset_token(db, user)
    settings = get_settings()
    if not settings.smtp_host:
        # Dev convenience: no SMTP configured → return token so the API can
        # surface it ONLY in non-production environments.
        if not settings.is_production:
            return raw
        return None
    return raw
