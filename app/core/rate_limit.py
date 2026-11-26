"""In-memory sliding-window rate limit per client IP, to protect the free LLM
quota on the public demo. Use Redis for multi-instance deployments."""
import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import HTTPException, Request

from app.core.settings import get_settings

_hits: dict[str, deque] = defaultdict(deque)
_lock = Lock()


def rate_limit(request: Request) -> None:
    limit = get_settings().rate_limit_per_minute
    if limit <= 0:
        return
    forwarded = request.headers.get("x-forwarded-for", "")
    key = forwarded.split(",")[0].strip() or (request.client.host if request.client else "unknown")
    now = time.monotonic()
    with _lock:
        window = _hits[key]
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= limit:
            raise HTTPException(status_code=429, detail=f"Rate limit: {limit} questions per minute. Try again shortly.")
        window.append(now)
