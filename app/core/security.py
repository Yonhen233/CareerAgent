from fastapi import Header, HTTPException, status

from app.core.config import get_settings


def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    settings = get_settings()
    if not settings.admin_api_key:
        return
    if x_admin_token == settings.admin_api_key:
        return
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Admin token is required for this operation.",
    )


def require_mutation_admin(x_admin_token: str | None = Header(default=None)) -> None:
    settings = get_settings()
    if not settings.require_admin_for_mutations:
        return
    require_admin(x_admin_token)
