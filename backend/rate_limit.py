"""Shared slowapi limiter — imported by main.py and every router that needs it.

Auth-aware key function: when the request carries a valid Bearer token we
bucket by user_id (so legitimate doctors don't share quota with anonymous
clients on the same NAT'd IP). Otherwise we fall back to the source IP.
"""
from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address


def _auth_aware_key(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth.split(" ", 1)[1].strip()
        # Local import to avoid circular: auth.py imports from rate_limit
        try:
            from auth import decode_token
            uid = decode_token(token)
            if uid is not None:
                return f"user:{uid}"
        except Exception:
            pass
    return f"ip:{get_remote_address(request)}"


limiter = Limiter(key_func=_auth_aware_key)
