"""Persistent email OTP challenges used by the existing account login flow."""

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import secrets

from pymongo import ReturnDocument

from storage.mongo_client import get_collection

OTP_TTL = timedelta(minutes=5)
RESEND_INTERVAL = timedelta(seconds=60)
MAX_ATTEMPTS = 5
MAX_RESENDS = 5


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _challenge_key(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _code_hash(secret: str, code: str) -> str:
    return hmac.new(secret.encode("utf-8"), code.encode("ascii"), hashlib.sha256).hexdigest()


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def start_challenge(user: dict) -> tuple[str, str]:
    """Persist a challenge and return its browser secret and emailed code."""
    secret = secrets.token_urlsafe(32)
    code = f"{secrets.randbelow(1_000_000):06d}"
    now = _now()
    get_collection("auth_challenges").insert_one({
        "challenge_key": _challenge_key(secret),
        "user_id": user["id"],
        "email": user["email"],
        "otp_hash": _code_hash(secret, code),
        "created_at": now,
        "expires_at": now + OTP_TTL,
        "resend_available_at": now + RESEND_INTERVAL,
        "attempts": 0,
        "resends": 0,
        "used_at": None,
    })
    return secret, code


def get_challenge(secret: str | None) -> dict | None:
    if not secret:
        return None
    challenge = get_collection("auth_challenges").find_one({
        "challenge_key": _challenge_key(secret), "used_at": None,
    })
    if not challenge:
        return None
    expires_at = _aware(challenge["expires_at"])
    resend_at = _aware(challenge["resend_available_at"])
    return {
        "email": _masked_email(challenge["email"]),
        "expired": expires_at <= _now(),
        "expires_at": expires_at.isoformat(),
        "resend_available_at": resend_at.isoformat(),
        "can_resend": challenge.get("resends", 0) < MAX_RESENDS,
        "attempts_remaining": max(0, MAX_ATTEMPTS - challenge.get("attempts", 0)),
    }


def _masked_email(email: str) -> str:
    local, _, domain = email.partition("@")
    return f"{local[:1]}{'*' * max(2, min(len(local) - 1, 6))}@{domain}"


def verify_challenge(secret: str | None, code: str) -> tuple[str, str | None]:
    """Return (result, user_id); result is valid, invalid, expired, locked, or used."""
    if not secret:
        return "missing", None
    challenges = get_collection("auth_challenges")
    key = _challenge_key(secret)
    challenge = challenges.find_one({"challenge_key": key})
    if not challenge:
        return "missing", None
    if challenge.get("used_at") is not None:
        return "used", None
    now = _now()
    if _aware(challenge["expires_at"]) <= now:
        return "expired", None
    if challenge.get("attempts", 0) >= MAX_ATTEMPTS:
        return "locked", None
    if not isinstance(code, str) or len(code) != 6 or not code.isascii() or not code.isdigit():
        valid = False
    else:
        valid = hmac.compare_digest(challenge.get("otp_hash", ""), _code_hash(secret, code))
    if not valid:
        update_result = challenges.update_one(
            {"challenge_key": key, "used_at": None, "attempts": {"$lt": MAX_ATTEMPTS}},
            {"$inc": {"attempts": 1}},
        )
        if update_result.modified_count:
            updated = challenges.find_one({"challenge_key": key}, {"attempts": 1})
            if updated and updated.get("attempts", 0) >= MAX_ATTEMPTS:
                return "locked", None
        return "invalid", None

    consumed = challenges.find_one_and_update(
        {
            "challenge_key": key,
            "otp_hash": challenge["otp_hash"],
            "used_at": None,
            "expires_at": {"$gt": now},
            "attempts": {"$lt": MAX_ATTEMPTS},
        },
        {"$set": {"used_at": now}},
        return_document=ReturnDocument.AFTER,
    )
    if not consumed:
        return "used", None
    return "valid", str(consumed["user_id"])


def resend_challenge(secret: str | None) -> tuple[str, str | None, str | None]:
    """Return (result, email, code), enforcing cooldown and resend cap."""
    if not secret:
        return "missing", None, None
    challenges = get_collection("auth_challenges")
    key = _challenge_key(secret)
    challenge = challenges.find_one({"challenge_key": key, "used_at": None})
    if not challenge:
        return "missing", None, None
    if challenge.get("resends", 0) >= MAX_RESENDS:
        return "limit", None, None
    now = _now()
    if _aware(challenge["resend_available_at"]) > now:
        return "cooldown", None, None

    code = f"{secrets.randbelow(1_000_000):06d}"
    updated = challenges.find_one_and_update(
        {
            "challenge_key": key,
            "used_at": None,
            "resends": {"$lt": MAX_RESENDS},
            "resend_available_at": {"$lte": now},
        },
        {"$set": {
            "otp_hash": _code_hash(secret, code),
            "created_at": now,
            "expires_at": now + OTP_TTL,
            "resend_available_at": now + RESEND_INTERVAL,
            "attempts": 0,
        }, "$inc": {"resends": 1}},
        return_document=ReturnDocument.AFTER,
    )
    if not updated:
        return "cooldown", None, None
    return "sent", updated["email"], code


def cancel_challenge(secret: str | None) -> None:
    if secret:
        get_collection("auth_challenges").delete_one({"challenge_key": _challenge_key(secret)})


def allow_immediate_resend(secret: str | None) -> None:
    """Remove the resend delay when SMTP delivery failed."""
    if secret:
        get_collection("auth_challenges").update_one(
            {"challenge_key": _challenge_key(secret), "used_at": None},
            {"$set": {"resend_available_at": _now()}},
        )
