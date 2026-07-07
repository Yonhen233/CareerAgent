from dataclasses import dataclass

from fastapi import Header, HTTPException, status

from app.core.config import get_settings


@dataclass(frozen=True)
class AuthContext:
    tenant_id: str
    user_id: str | None
    roles: set[str]
    auth_type: str

    @property
    def actor(self) -> str:
        return self.user_id or self.auth_type


def parse_auth_context(
    *,
    x_tenant_id: str | None = None,
    x_user_id: str | None = None,
    x_user_roles: str | None = None,
    x_admin_token: str | None = None,
) -> AuthContext:
    settings = get_settings()
    roles = {role.strip().lower() for role in (x_user_roles or "").split(",") if role.strip()}
    if settings.admin_api_key and x_admin_token == settings.admin_api_key:
        roles |= settings.rbac_admin_role_set
        return AuthContext(
            tenant_id=x_tenant_id or settings.rbac_default_tenant_id,
            user_id=x_user_id or "admin-token",
            roles=roles,
            auth_type="admin_token",
        )
    return AuthContext(
        tenant_id=x_tenant_id or settings.rbac_default_tenant_id,
        user_id=x_user_id,
        roles=roles,
        auth_type="trusted_header" if roles or x_user_id else "anonymous",
    )


def has_admin_access(context: AuthContext) -> bool:
    settings = get_settings()
    return bool(context.roles & settings.rbac_admin_role_set)


def require_admin(
    x_admin_token: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
    x_user_roles: str | None = Header(default=None),
) -> AuthContext:
    settings = get_settings()
    context = parse_auth_context(
        x_tenant_id=x_tenant_id,
        x_user_id=x_user_id,
        x_user_roles=x_user_roles,
        x_admin_token=x_admin_token,
    )
    if not settings.admin_api_key and not settings.rbac_enabled:
        return context
    if has_admin_access(context):
        return context
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Admin token or RBAC admin role is required for this operation.",
    )


def require_mutation_admin(
    x_admin_token: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
    x_user_roles: str | None = Header(default=None),
) -> AuthContext | None:
    settings = get_settings()
    if not settings.require_admin_for_mutations:
        return parse_auth_context(
            x_tenant_id=x_tenant_id,
            x_user_id=x_user_id,
            x_user_roles=x_user_roles,
            x_admin_token=x_admin_token,
        )
    return require_admin(
        x_admin_token=x_admin_token,
        x_tenant_id=x_tenant_id,
        x_user_id=x_user_id,
        x_user_roles=x_user_roles,
    )


def request_has_mutation_access(headers) -> bool:
    settings = get_settings()
    if not settings.require_admin_for_mutations:
        return True
    context = parse_auth_context(
        x_tenant_id=headers.get("x-tenant-id"),
        x_user_id=headers.get("x-user-id"),
        x_user_roles=headers.get("x-user-roles"),
        x_admin_token=headers.get("x-admin-token"),
    )
    return has_admin_access(context)
