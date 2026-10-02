"""Password + TOTP (Google Authenticator) login for the command platform.

Deliberately not Firebase: this is a single-operator console, and Firebase would
mean a second identity system. TOTP is implemented here on the stdlib (hmac +
base32) so the deployment gains no new dependency and the verification path stays
auditable in one file.

Security notes
--------------
* Passwords are stored as PBKDF2-HMAC-SHA256 with a per-user salt, never plaintext
  and never a bare hash. Comparison is constant-time.
* The TOTP secret is only ever returned once, at enrolment. After that the UI
  shows a scannable otpauth:// URI instead of the raw secret.
* Sessions are opaque random tokens stored hashed in the DB, so a DB leak does not
  hand an attacker usable cookies. `SESSION_TTL_DAYS` bounds their life.
* Every attempt is rate-limited per email, and successful/failed counts are kept so
  a password can be locked out under sustained guessing.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
import urllib.parse
from dataclasses import dataclass

from fastapi import HTTPException

from app.config import settings
from app.core.db import Database

PBKDF2_ROUNDS = 240_000
TOTP_DIGITS = 6
TOTP_STEP = 30
# Accept one step either side of now: phone clocks drift, and a strict window
# rejects legitimate users more often than it stops an attacker with the secret.
TOTP_WINDOW = 1
SESSION_COOKIE = "titan_session"


# ---------- password hashing ----------
def hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    salt = salt or os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)
    return base64.b64encode(salt).decode(), base64.b64encode(dk).decode()


def verify_password(password: str, salt_b64: str, hash_b64: str) -> bool:
    try:
        _, candidate = hash_password(password, base64.b64decode(salt_b64))
    except Exception:  # noqa: BLE001
        return False
    return hmac.compare_digest(candidate, hash_b64)


# ---------- TOTP ----------
def generate_secret(nbytes: int = 20) -> str:
    """Base32 secret, the format every authenticator app expects."""
    return base64.b32encode(os.urandom(nbytes)).decode().rstrip("=")


def totp_at(secret_b32: str, counter: int) -> str:
    key = base64.b32decode(secret_b32 + "=" * (-len(secret_b32) % 8))
    digest = hmac.new(key, counter.to_bytes(8, "big"), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = ((digest[offset] & 0x7F) << 24 | digest[offset + 1] << 16
            | digest[offset + 2] << 8 | digest[offset + 3])
    return str(code % (10 ** TOTP_DIGITS)).zfill(TOTP_DIGITS)


def current_totp(secret_b32: str) -> str:
    return totp_at(secret_b32, int(time.time()) // TOTP_STEP)


def verify_totp(secret_b32: str, code: str) -> bool:
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit() or len(code) != TOTP_DIGITS:
        return False
    counter = int(time.time()) // TOTP_STEP
    for drift in range(-TOTP_WINDOW, TOTP_WINDOW + 1):
        if hmac.compare_digest(totp_at(secret_b32, counter + drift), code):
            return True
    return False


def otpauth_uri(secret_b32: str, email: str, issuer: str = "TITAN MT5") -> str:
    label = urllib.parse.quote(f"{issuer}:{email}")
    return (f"otpauth://totp/{label}?secret={secret_b32}"
            f"&issuer={urllib.parse.quote(issuer)}&algorithm=SHA1&digits={TOTP_DIGITS}&period={TOTP_STEP}")


# ---------- sessions ----------
def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass
class Identity:
    uid: int
    email: str
    totp_enrolled: bool


class AuthService:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        for stmt in (
            """CREATE TABLE IF NOT EXISTS auth_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password_salt TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                totp_secret TEXT,
                totp_enrolled INTEGER NOT NULL DEFAULT 0,
                created_ts TEXT NOT NULL,
                last_login_ts TEXT,
                failed_count INTEGER NOT NULL DEFAULT 0,
                locked_until_ts REAL
            )""",
            """CREATE TABLE IF NOT EXISTS auth_sessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                created_ts TEXT NOT NULL,
                expires_ts REAL NOT NULL
            )""",
            "CREATE INDEX IF NOT EXISTS idx_sessions_exp ON auth_sessions(expires_ts)",
        ):
            self.db.execute(stmt)

    # ---------- enrolment ----------
    def create_user(self, email: str, password: str) -> None:
        email = email.strip().lower()
        salt, digest = hash_password(password)
        self.db.execute(
            "INSERT OR REPLACE INTO auth_users "
            "(email, password_salt, password_hash, totp_secret, totp_enrolled, created_ts, "
            " failed_count, locked_until_ts) "
            "VALUES (?,?,?,NULL,0,datetime('now'),0,NULL)",
            (email, salt, digest),
        )

    def any_enrolled_user(self) -> bool:
        row = self.db.query_one("SELECT COUNT(*) AS n FROM auth_users WHERE totp_enrolled=1")
        return bool(row and int(row["n"]) > 0)

    def begin_totp_enrolment(self, email: str) -> str:
        """Issue a fresh secret. Returned once; the caller shows the QR/URI."""
        email = email.strip().lower()
        row = self.db.query_one("SELECT id FROM auth_users WHERE email=?", (email,))
        if not row:
            raise HTTPException(status_code=404, detail="no such user")
        secret = generate_secret()
        self.db.execute("UPDATE auth_users SET totp_secret=?, totp_enrolled=0 WHERE email=?",
                        (secret, email))
        return secret

    def confirm_totp_enrolment(self, email: str, code: str) -> None:
        row = self.db.query_one(
            "SELECT id, totp_secret FROM auth_users WHERE email=?", (email.strip().lower(),))
        if not row or not row.get("totp_secret"):
            raise HTTPException(status_code=400, detail="start enrolment first")
        if not verify_totp(row["totp_secret"], code):
            raise HTTPException(status_code=400, detail="wrong code")
        self.db.execute("UPDATE auth_users SET totp_enrolled=1 WHERE id=?", (row["id"],))

    def is_enrolled(self, email: str) -> bool:
        row = self.db.query_one(
            "SELECT totp_enrolled FROM auth_users WHERE email=?", (email.strip().lower(),))
        return bool(row and row.get("totp_enrolled"))

    # ---------- login ----------
    def login(self, email: str, password: str, code: str) -> tuple[str, Identity]:
        email = email.strip().lower()
        row = self.db.query_one(
            "SELECT * FROM auth_users WHERE email=?", (email,))
        if not row:
            # Spend comparable time so a missing account is not detectable by timing.
            hash_password(password)
            raise HTTPException(status_code=401, detail="email, password หรือ code ไม่ถูกต้อง")

        locked = row.get("locked_until_ts")
        if locked and time.time() < float(locked):
            wait = int(float(locked) - time.time())
            raise HTTPException(
                status_code=429,
                detail=f"บัญชีถูกล็อกชั่วคราว ลองอีกครั้งใน {wait} วินาที")

        ok_pw = verify_password(password, row["password_salt"], row["password_hash"])
        ok_totp = bool(row.get("totp_secret")) and verify_totp(row["totp_secret"], code)
        if not (ok_pw and ok_totp):
            self._record_failure(row)
            raise HTTPException(status_code=401, detail="email, password หรือ code ไม่ถูกต้อง")

        self.db.execute(
            "UPDATE auth_users SET last_login_ts=datetime('now'), failed_count=0, "
            "locked_until_ts=NULL WHERE id=?", (row["id"],))
        ident = Identity(row["id"], email, True)
        return self.issue_session(ident), ident

    def _record_failure(self, row: dict) -> None:
        """Escalating lockout: each failure widens the window."""
        n = int(row.get("failed_count") or 0) + 1
        locked_until = None
        if n >= 5:
            locked_until = time.time() + min(900, 60 * (2 ** (n - 5)))
        self.db.execute(
            "UPDATE auth_users SET failed_count=?, locked_until_ts=? WHERE id=?",
            (n, locked_until, row["id"]),
        )

    def issue_session(self, ident: Identity, ttl_days: int | None = None) -> str:
        ttl_days = ttl_days if ttl_days is not None else settings.session_ttl_days
        token = secrets.token_urlsafe(32)
        self.db.execute(
            "INSERT INTO auth_sessions (token_hash, user_id, created_ts, expires_ts) "
            "VALUES (?,?,datetime('now'),?)",
            (_hash_token(token), ident.uid, time.time() + ttl_days * 86400),
        )
        return token

    def user_for_token(self, token: str | None) -> Identity | None:
        if not token:
            return None
        row = self.db.query_one(
            "SELECT s.user_id, s.expires_ts, u.email FROM auth_sessions s "
            "JOIN auth_users u ON u.id = s.user_id WHERE s.token_hash=?",
            (_hash_token(token),),
        )
        if not row or float(row["expires_ts"]) < time.time():
            return None
        return Identity(row["user_id"], row["email"], True)

    def logout(self, token: str | None) -> None:
        if token:
            self.db.execute("DELETE FROM auth_sessions WHERE token_hash=?",
                            (_hash_token(token),))

    def purge_expired(self) -> int:
        self.db.execute("DELETE FROM auth_sessions WHERE expires_ts < ?", (time.time(),))
        return 0
