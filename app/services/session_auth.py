from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.entities import AppUser, Tenant


class SessionAuthError(RuntimeError):
    pass


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64url(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


class SessionAuthService:
    def __init__(self, *, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def hash_password(self, password: str, *, salt: str | None = None) -> str:
        salt = salt or _b64url(os.urandom(16))
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            self.settings.session_password_iterations,
        )
        return f"pbkdf2_sha256${self.settings.session_password_iterations}${salt}${_b64url(digest)}"

    def verify_password(self, password: str, encoded: str | None) -> bool:
        if not encoded:
            return False
        try:
            algorithm, iterations, salt, expected = encoded.split("$", 3)
        except ValueError:
            return False
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), int(iterations))
        return hmac.compare_digest(_b64url(digest), expected)

    def create_session_token(self, *, tenant_id: str, user_id: str, roles: list[str]) -> str:
        payload = {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "roles": sorted(set(roles)),
            "exp": int(time.time()) + int(self.settings.session_ttl_seconds),
        }
        raw = _b64url(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        signature = self._sign(raw)
        return f"{raw}.{signature}"

    def decode_session_token(self, token: str | None) -> dict[str, Any] | None:
        if not token or "." not in token:
            return None
        raw, signature = token.rsplit(".", 1)
        if not hmac.compare_digest(self._sign(raw), signature):
            return None
        try:
            payload = json.loads(_unb64url(raw).decode("utf-8"))
        except Exception:
            return None
        if int(payload.get("exp") or 0) < int(time.time()):
            return None
        return payload

    def login(self, db: Session, *, tenant_slug: str, email: str, password: str) -> tuple[str, AppUser, Tenant]:
        tenant = db.query(Tenant).filter(Tenant.slug == tenant_slug, Tenant.status == "active").first()
        if tenant is None:
            raise SessionAuthError("Tenant not found or inactive.")
        user = (
            db.query(AppUser)
            .filter(AppUser.tenant_id == tenant.id, AppUser.email == email, AppUser.status == "active")
            .first()
        )
        if user is None or not self.verify_password(password, user.password_hash):
            raise SessionAuthError("Invalid email or password.")
        token = self.create_session_token(
            tenant_id=tenant.slug,
            user_id=user.external_user_id,
            roles=[str(role).lower() for role in user.roles_json or []],
        )
        return token, user, tenant

    def ensure_bootstrap_admin(self, db: Session) -> AppUser | None:
        email = self.settings.session_bootstrap_admin_email
        password = self.settings.session_bootstrap_admin_password
        if not email or not password:
            return None
        tenant = db.query(Tenant).filter(Tenant.slug == self.settings.rbac_default_tenant_id).first()
        if tenant is None:
            tenant = Tenant(slug=self.settings.rbac_default_tenant_id, name=self.settings.rbac_default_tenant_id)
            db.add(tenant)
            db.commit()
            db.refresh(tenant)
        existing = db.query(AppUser).filter(AppUser.tenant_id == tenant.id, AppUser.email == email).first()
        if existing is not None:
            return existing
        user = AppUser(
            tenant_id=tenant.id,
            external_user_id=email,
            email=email,
            password_hash=self.hash_password(password),
            roles_json=["owner", "admin", "ops"],
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user

    def _sign(self, raw: str) -> str:
        digest = hmac.new(self.settings.session_secret_key.encode("utf-8"), raw.encode("utf-8"), hashlib.sha256)
        return _b64url(digest.digest())
