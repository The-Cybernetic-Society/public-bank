"""
Sign-in with a one-time code on WhatsApp, for cyberneticsociety.com and any other page that is not this service.

Who may sign in: a number on our canonical list, which is the members who joined on WhatsApp and the numbers
businesses registered with on the form. Nothing else is asked for and no other number gets a code.

  start(con, phone)         → a six-digit code, stored hashed with a ten-minute life; the caller sends it
  verify(con, phone, code)  → the member, once the code matches (five wrong guesses kill the code)
  mint(member_id) / parse(token) → a signed bearer token the static site keeps, good for ninety days

Limits: three codes per number per hour, five guesses per code. The code is never stored or logged in clear;
the hash is keyed with PB_SECRET so a copy of the database alone cannot be replayed.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from . import core, db

SECRET = os.environ.get("PB_SECRET", "dev-secret-change-me")
CODE_TTL = 10 * 60
MAX_ATTEMPTS = 5
MAX_SENDS_PER_HOUR = 3
TOKEN_MAX_AGE = 90 * 24 * 3600
_bearer = URLSafeTimedSerializer(SECRET, salt="pb-bearer")


def _hash(phone: str, code: str) -> str:
    return hmac.new(SECRET.encode(), f"{phone}:{code}".encode(), hashlib.sha256).hexdigest()


def start(con, phone: str) -> str:
    """Make a fresh code for a known number. Raises unknown_phone or too_many_codes."""
    if not core.phone_registered(con, phone):
        raise core.PBError("unknown_phone")
    r = db.row(con.execute("SELECT * FROM otp WHERE phone=?", (phone,)).fetchone())
    t = db.now()
    sends = [x for x in db.j(r["sends"], []) if x > t - 3600] if r else []
    if len(sends) >= MAX_SENDS_PER_HOUR:
        raise core.PBError("too_many_codes", str(3600 - (t - sends[0])))
    code = f"{secrets.randbelow(1_000_000):06d}"
    sends.append(t)
    with db.tx(con):
        con.execute("""INSERT INTO otp (phone,code_hash,expires_at,attempts,sends,updated_at) VALUES (?,?,?,?,?,?)
                       ON CONFLICT(phone) DO UPDATE SET code_hash=excluded.code_hash, expires_at=excluded.expires_at,
                       attempts=0, sends=excluded.sends, updated_at=excluded.updated_at""",
                    (phone, _hash(phone, code), t + CODE_TTL, 0, json.dumps(sends), t))
    return code


def verify(con, phone: str, code: str) -> dict:
    """The member the number belongs to, once the code matches. Raises bad_code (with how many guesses remain),
    code_expired, or too_many_attempts."""
    code = "".join(ch for ch in str(code) if ch.isdigit())
    r = db.row(con.execute("SELECT * FROM otp WHERE phone=?", (phone,)).fetchone())
    if not r or r["expires_at"] < db.now():
        raise core.PBError("code_expired")
    if r["attempts"] >= MAX_ATTEMPTS:
        raise core.PBError("too_many_attempts")
    if len(code) != 6 or not hmac.compare_digest(r["code_hash"], _hash(phone, code)):
        with db.tx(con):
            con.execute("UPDATE otp SET attempts=attempts+1, updated_at=? WHERE phone=?", (db.now(), phone))
        left = MAX_ATTEMPTS - r["attempts"] - 1
        raise core.PBError("too_many_attempts" if left <= 0 else "bad_code", str(left))
    with db.tx(con):   # spent, but the send times stay so the hourly limit still counts them
        con.execute("UPDATE otp SET expires_at=0, updated_at=? WHERE phone=?", (db.now(), phone))
    return core.sign_in_by_phone(con, phone)


def mint(member_id: str) -> str:
    return _bearer.dumps({"m": member_id})


def parse(token: str) -> str | None:
    """The member id inside a bearer token, or None when it is not ours or too old."""
    try:
        return _bearer.loads(token, max_age=TOKEN_MAX_AGE)["m"]
    except (BadSignature, SignatureExpired, KeyError, TypeError):
        return None
