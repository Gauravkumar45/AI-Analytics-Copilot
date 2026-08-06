import os
import time
from typing import Optional

import jwt

SECRET_KEY = os.environ.get("SECRET_KEY", "change-me")
ALGORITHM = os.environ.get("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_SECONDS = 3600


class AuthService:
    def authenticate_user(self, username: str, password: str) -> Optional[str]:
        if username == "admin" and password == "password":
            payload = {
                "sub": username,
                "iat": int(time.time()),
                "exp": int(time.time()) + ACCESS_TOKEN_EXPIRE_SECONDS,
            }
            return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
        return None
