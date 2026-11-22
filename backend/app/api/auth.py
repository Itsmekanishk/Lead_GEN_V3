"""Authentication & account endpoints."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session as DBSession

from app.core.deps import CurrentUser, bearer_scheme
from app.core.rate_limiter import check_rate_limit, RateLimit
from app.models.database import get_db
from app.schemas.auth import (
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    ResetPasswordRequest,
    TokenResponse,
    UserOut,
)
from app.services import auth_service
from app.services.auth_service import AuthError

router = APIRouter(prefix="/api/auth", tags=["auth"])
bearer = HTTPBearer(auto_error=False)

# Token responses must never be stored by a browser cache, a proxy or a CDN.
_NO_STORE = "no-store"


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "anonymous"


def _loopback_exempt(request: Request) -> bool:
    """Dev/load-test opt-out (RATE_LIMIT_DISABLE_LOOPBACK=true): skip per-IP
    auth limits for loopback clients so local scripts aren't throttled.
    Default is OFF — production limits always apply."""
    import os

    if os.environ.get("RATE_LIMIT_DISABLE_LOOPBACK", "false") != "true":
        return False
    return _client_ip(request) in ("127.0.0.1", "::1", "testclient")


def _enforce(request: Request, name: str, max_requests: int, window: int) -> None:
    """Apply a per-IP limit to an unauthenticated endpoint."""
    if _loopback_exempt(request):
        return
    allowed, retry = check_rate_limit(
        f"{name}:{_client_ip(request)}", RateLimit(name, max_requests, window)
    )
    if not allowed:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many attempts; try again later",
            headers={"Retry-After": str(retry)},
        )


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(body: RegisterRequest, request: Request, response: Response,
             db: DBSession = Depends(get_db)) -> TokenResponse:
    """Create an account and receive tokens (first user becomes ADMIN)."""
    _enforce(request, "register", 5, 3600)
    try:
        user = auth_service.create_user(
            db, email=body.email, password=body.password, full_name=body.full_name
        )
    except AuthError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    access = auth_service.login_tokens(db, user)  # type: ignore[attr-defined]
    response.headers["Cache-Control"] = _NO_STORE
    return access


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, request: Request, response: Response,
          db: DBSession = Depends(get_db)) -> TokenResponse:
    """Authenticate with email+password; returns access+refresh tokens."""
    _enforce(request, "login", 10, 300)
    try:
        user = auth_service.authenticate(db, body.email, body.password)
    except AuthError:
        # same message for unknown user and wrong password (no enumeration)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    auth_service.audit(db, user_id=user.id, action="login", ip=_client_ip(request))
    access = auth_service.login_tokens(db, user)  # type: ignore[attr-defined]
    db.commit()
    response.headers["Cache-Control"] = _NO_STORE
    return access


@router.post("/session", response_model=TokenResponse)
def auto_session(request: Request, response: Response,
                db: DBSession = Depends(get_db)) -> TokenResponse:
    """Issue a session without a sign-in form.

    The app has no login page, so the browser calls this once when it holds no
    tokens. Every browser gets its own anonymous workspace (a fresh guest
    account), so two people on the same deployment never see each other's
    searches.
    """
    import secrets

    from app.models.models import UserRole

    _enforce(request, "session", 20, 300)
    user = auth_service.create_user(
        db,
        email=f"guest-{secrets.token_hex(6)}@guest.locallead.app",
        password=secrets.token_urlsafe(32),
        full_name="Guest",
        role=UserRole.USER,
    )
    auth_service.audit(db, user_id=user.id, action="auto_session", ip=_client_ip(request))
    tokens = auth_service.login_tokens(db, user)  # type: ignore[attr-defined]
    db.commit()
    response.headers["Cache-Control"] = _NO_STORE
    return tokens


@router.post("/refresh", response_model=TokenResponse)
def refresh(body: RefreshRequest, request: Request, response: Response,
            db: DBSession = Depends(get_db)) -> TokenResponse:
    """Rotate the refresh token and mint a new access token."""
    _enforce(request, "refresh", 60, 300)
    try:
        user, new_refresh = auth_service.rotate_refresh_token(db, body.refresh_token)
    except AuthError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    from app.core.security import create_access_token

    response.headers["Cache-Control"] = _NO_STORE
    return TokenResponse(
        access_token=create_access_token(user.id, user.role),
        refresh_token=new_refresh,
        user=UserOut.model_validate(user),
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(body: RefreshRequest,
           credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
           db: DBSession = Depends(get_db)) -> None:
    """Revoke the presented refresh token."""
    try:
        auth_service.revoke_refresh_token(db, body.refresh_token)
    except AuthError:
        pass  # logout is idempotent


@router.post("/forgot-password", response_model=ForgotPasswordResponse)
def forgot_password(body: ForgotPasswordRequest, request: Request,
                    db: DBSession = Depends(get_db)) -> ForgotPasswordResponse:
    """Begin password reset. Response never reveals account existence."""
    _enforce(request, "forgot", 5, 900)
    token = auth_service.request_password_reset(db, body.email)
    from app.core.config import get_settings

    dev_token = token if (token and not get_settings().is_production) else None
    return ForgotPasswordResponse(
        message="If the account exists, a reset link has been sent.",
        dev_token=dev_token,
    )


@router.post("/reset-password", response_model=UserOut)
def reset_password(body: ResetPasswordRequest, request: Request,
                   db: DBSession = Depends(get_db)) -> UserOut:
    """Complete password reset with a valid token."""
    _enforce(request, "reset", 5, 900)
    try:
        user = auth_service.consume_password_reset_token(db, body.token, body.new_password)
    except AuthError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return UserOut.model_validate(user)


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> UserOut:
    """Return the authenticated user's profile."""
    return UserOut.model_validate(user)
