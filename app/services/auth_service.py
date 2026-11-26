import hmac
import os
import secrets
import time
from typing import Optional

import jwt

# A random key per process when SECRET_KEY is unset: tokens simply expire on restart.
SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_urlsafe(32)
ALGORITHM = os.environ.get("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_SECONDS = 3600


class AuthNotConfigured(RuntimeError):
    pass


class AuthService:
    """Single admin account from environment variables (no hardcoded credentials)."""

    def authenticate_user(self, username: str, password: str) -> Optional[str]:
        expected_user = os.environ.get("ADMIN_USERNAME")
        expected_pass = os.environ.get("ADMIN_PASSWORD")
        if not expected_user or not expected_pass:
            raise AuthNotConfigured("Set ADMIN_USERNAME and ADMIN_PASSWORD to enable login")
        ok = hmac.compare_digest(username, expected_user) and hmac.compare_digest(password, expected_pass)
        if not ok:
            return None
        now = int(time.time())
        return jwt.encode({"sub": username, "iat": now, "exp": now + ACCESS_TOKEN_EXPIRE_SECONDS},
                          SECRET_KEY, algorithm=ALGORITHM)
