from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import AuthContext, optional_auth_context
from app.models.schemas import AuthSessionResponse, LoginRequest
from app.services.session_auth import SessionAuthError, SessionAuthService

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=AuthSessionResponse)
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)) -> AuthSessionResponse:
    settings = get_settings()
    try:
        token, user, tenant = SessionAuthService(settings=settings).login(
            db,
            tenant_slug=payload.tenant_id,
            email=payload.email,
            password=payload.password,
        )
    except SessionAuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        samesite="lax",
    )
    return AuthSessionResponse(
        tenant_id=tenant.slug,
        user_id=user.external_user_id,
        email=user.email,
        roles=[str(role) for role in user.roles_json or []],
    )


@router.post("/logout")
def logout(response: Response) -> dict:
    settings = get_settings()
    response.delete_cookie(settings.session_cookie_name)
    return {"status": "logged_out"}


@router.get("/me", response_model=AuthSessionResponse)
def me(auth: AuthContext = Depends(optional_auth_context)) -> AuthSessionResponse:
    return AuthSessionResponse(
        tenant_id=auth.tenant_id,
        user_id=auth.user_id or "anonymous",
        roles=sorted(auth.roles),
        auth_type=auth.auth_type,
    )
