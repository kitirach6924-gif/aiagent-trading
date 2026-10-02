"""Firebase ID token verification for FastAPI.

Dashboard sends `Authorization: Bearer <firebase id token>`. We verify against
Google's public certificates (JWKS) — no Firebase Admin SDK service account needed.
Set AUTH_DISABLED=true only for local development.
"""
from __future__ import annotations

import json
import time
import urllib.request
import base64

from fastapi import Header, HTTPException, Request

from app.config import settings

# Bound in main.py at startup. Imported lazily to avoid a circular import: the
# AuthService needs settings, and require_user needs the service.
platform_auth = None
SESSION_COOKIE = "titan_session"

_JWKS_URL = "https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com"
_jwks_cache: tuple[float, dict] | None = None


def _b64url_decode(s: str) -> bytes:
    s += "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s)


def _get_jwks() -> dict:
    global _jwks_cache
    if _jwks_cache is None or time.time() - _jwks_cache[0] > 3600:
        with urllib.request.urlopen(_JWKS_URL, timeout=10) as resp:
            certs = json.loads(resp.read().decode())
        _jwks_cache = (time.time(), certs)
    return _jwks_cache[1]


def _verify_firebase_id_token(token: str) -> dict:
    header_b64, payload_b64, sig_b64 = token.split(".")
    header = json.loads(_b64url_decode(header_b64))
    payload = json.loads(_b64url_decode(payload_b64))
    if header.get("alg") != "RS256":
        raise HTTPException(status_code=401, detail="unsupported alg")
    if payload.get("aud") != settings.firebase_project_id:
        raise HTTPException(status_code=401, detail="wrong audience")
    if payload.get("iss") != f"https://securetoken.google.com/{settings.firebase_project_id}":
        raise HTTPException(status_code=401, detail="wrong issuer")
    if time.time() > int(payload.get("exp", 0)):
        raise HTTPException(status_code=401, detail="token expired")
    # signature check
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.exceptions import InvalidSignature
    except ImportError:
        return payload  # pragma: no cover - allow without crypto lib only in dev
    jwks = _get_jwks()
    kid = header.get("kid")
    if kid not in jwks:
        raise HTTPException(status_code=401, detail="unknown key id")
    cert_pem = jwks[kid].encode()
    cert = x509_load(cert_pem)
    pub = cert.public_key()
    try:
        pub.verify(_b64url_decode(sig_b64), f"{header_b64}.{payload_b64}".encode(),
                   padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature:
        raise HTTPException(status_code=401, detail="bad signature")
    return payload


def x509_load(pem: bytes):
    from cryptography import x509
    return x509.load_pem_x509_certificate(pem)


async def require_user(request: Request, authorization: str | None = Header(default=None)) -> dict:
    """Resolve the caller.

    Two schemes coexist. When PLATFORM_AUTH_ENABLED is on, the browser session
    cookie issued by the platform login is the primary path and the Firebase
    bearer token is the fallback for the existing dashboard clients. AUTH_DISABLED
    stays the local-dev escape hatch and wins over both.

    `request` is annotated as Request with no default on purpose: with a default
    of None FastAPI passes the literal None through instead of the request object,
    and the cookie check silently degrades to "never signed in".
    """
    if settings.auth_disabled:
        return {"uid": "dev-local", "email": "dev@local"}
    # A valid platform session wins. Otherwise fall through to the Firebase bearer
    # token so the older React dashboard keeps working alongside the new login.
    if settings.platform_auth_enabled and request is not None:
        ident = platform_auth.user_for_token(request.cookies.get(SESSION_COOKIE))
        if ident:
            return {"uid": ident.uid, "email": ident.email}
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="ยังไม่ได้เข้าสู่ระบบ")
    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = _verify_firebase_id_token(token)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=401, detail=f"invalid token: {e}")
    return {"uid": payload.get("user_id") or payload.get("sub"), "email": payload.get("email", "")}
