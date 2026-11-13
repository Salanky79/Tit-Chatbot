"""Persistent email OTP challenges used by the existing account login flow.

Module quản lý toàn bộ vòng đời của thử thách mã xác thực OTP qua Email (2FA / Verification):
- Sử dụng Cookie HttpOnly ('tit_auth_challenge') chứa browser secret 32 bytes gửi về client.
- Database chỉ lưu giá trị băm SHA-256 của secret ('challenge_key'), chống rò rỉ secret kể cả khi lộ database.
- Mã OTP 6 chữ số được băm bằng HMAC-SHA256 với secret làm khóa ký ('otp_hash').
- Hỗ trợ khôi phục giao diện người dùng (Restore UI) khi tải lại trang hoặc mở lại modal.
- Cơ chế kiểm soát số lần thử (brute-force defense), cooldown 60s và giới hạn gửi lại tối đa 5 lần.
"""

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import secrets

from pymongo import ReturnDocument

from storage.mongo_client import get_collection

# --- CÁC HẰNG SỐ CẤU HÌNH & BẢO MẬT ---
OTP_TTL = timedelta(minutes=5)           # Thời hạn hiệu lực của mã OTP (5 phút)
RESEND_INTERVAL = timedelta(seconds=60)  # Thời gian chờ tối thiểu giữa 2 lần gửi lại mã (Cooldown 60 giây)
MAX_ATTEMPTS = 5                         # Số lần nhập sai tối đa trước khi khóa thử thách (Chống Brute-Force dò mã)
MAX_RESENDS = 5                          # Số lần tối đa được phép bấm gửi lại mã trên cùng một thử thách


def _now() -> datetime:
    """Trả về thời gian hiện tại theo chuẩn UTC (timezone-aware)."""
    return datetime.now(timezone.utc)


def _challenge_key(secret: str) -> str:
    """Băm chuỗi secret của trình duyệt bằng SHA-256 để làm khóa tra cứu trong MongoDB (không lưu secret gốc)."""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _code_hash(secret: str, code: str) -> str:
    """Băm mã OTP 6 số bằng HMAC-SHA256, sử dụng secret của trình duyệt làm khóa ký bí mật."""
    return hmac.new(secret.encode("utf-8"), code.encode("ascii"), hashlib.sha256).hexdigest()
# hợp nhất 

def _aware(value: datetime) -> datetime:
    """Chuẩn hóa đối tượng datetime đọc từ MongoDB về dạng có múi giờ UTC (tránh lỗi TypeError so sánh datetime)."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def start_challenge(user: dict) -> tuple[str, str]:
    """Khởi tạo thử thách OTP mới: sinh secret (gắn cookie) + sinh mã 6 số (gửi email), lưu MongoDB (hạn 5p, cooldown 60s)."""
    secret = secrets.token_urlsafe(32)
    code = f"{secrets.randbelow(1_000_000):06d}"
    now = _now()
    get_collection("auth_challenges").insert_one({
        "challenge_key": _challenge_key(secret),      # Khóa tra cứu đã băm SHA-256
        "user_id": user["id"],                        # ID của user đang xác minh
        "email": user["email"],                       # Địa chỉ email nhận mã
        "otp_hash": _code_hash(secret, code),         # Bản băm HMAC của mã OTP
        "created_at": now,                            # Thời điểm tạo thử thách
        "expires_at": now + OTP_TTL,                  # Thời điểm hết hạn (now + 5 phút)
        "resend_available_at": now + RESEND_INTERVAL, # Thời điểm được phép gửi lại (now + 60s)
        "attempts": 0,                                # Số lần đã nhập sai (bắt đầu từ 0)
        "resends": 0,                                 # Số lần đã bấm gửi lại mã
        "used_at": None,                              # Thời điểm mã được tiêu thụ thành công
    })
    return secret, code


def get_challenge(secret: str | None) -> dict | None:
    """Đọc cookie secret để khôi phục màn hình OTP khi người dùng F5 hoặc mở lại modal đăng nhập."""
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
        "email": _masked_email(challenge["email"]),                               # Email đã ẩn bớt ký tự
        "expired": expires_at <= _now(),                                          # Đã hết hạn 5 phút chưa?
        "expires_at": expires_at.isoformat(),                                     # Chuỗi ISO ngày hết hạn
        "resend_available_at": resend_at.isoformat(),                             # Chuỗi ISO thời điểm hết cooldown
        "can_resend": challenge.get("resends", 0) < MAX_RESENDS,                  # Còn lượt bấm gửi lại không? (< 5 lần)
        "attempts_remaining": max(0, MAX_ATTEMPTS - challenge.get("attempts", 0)), # Số lần nhập sai còn lại
    }


def _masked_email(email: str) -> str:
    """Che bớt ký tự của địa chỉ email để bảo đảm quyền riêng tư khi hiển thị (VD: t******@gmail.com)."""
    local, _, domain = email.partition("@")
    return f"{local[:1]}{'*' * max(2, min(len(local) - 1, 6))}@{domain}"


def verify_challenge(secret: str | None, code: str) -> tuple[str, str | None]:
    """Kiểm tra mã OTP 6 số người dùng nhập: sai thì tăng số lần thử (sai 5 lần -> locked), đúng thì tiêu thụ atomic và trả về user_id."""
    if not secret:
        return "missing", None
    challenges = get_collection("auth_challenges")
    key = _challenge_key(secret)
    challenge = challenges.find_one({"challenge_key": key})
    if not challenge:
        return "missing", None
    # Nếu mã này đã từng được xác thực thành công trước đó (chống Replay Attack)
    if challenge.get("used_at") is not None:
        return "used", None
    now = _now()
    # Kiểm tra hạn dùng 5 phút
    if _aware(challenge["expires_at"]) <= now:
        return "expired", None
    # Kiểm tra giới hạn số lần thử sai (tối đa 5 lần)
    if challenge.get("attempts", 0) >= MAX_ATTEMPTS:
        return "locked", None
    # Kiểm tra định dạng đầu vào: phải là chuỗi đúng 6 chữ số
    if not isinstance(code, str) or len(code) != 6 or not code.isascii() or not code.isdigit():
        valid = False
    else:
        # So sánh an toàn chống Timing Attack
        valid = hmac.compare_digest(challenge.get("otp_hash", ""), _code_hash(secret, code))
    
    # Trường hợp nhập sai mã OTP: Tăng số lần thử sai lên 1
    if not valid:
        update_result = challenges.update_one(
            {"challenge_key": key, "used_at": None, "attempts": {"$lt": MAX_ATTEMPTS}},
            {"$inc": {"attempts": 1}},
        )
        if update_result.modified_count:
            updated = challenges.find_one({"challenge_key": key}, {"attempts": 1})
            # Nếu chạm mốc 5 lần sai -> Khóa thử thách ngay lập tức
            if updated and updated.get("attempts", 0) >= MAX_ATTEMPTS:
                return "locked", None
        return "invalid", None

    # Trường hợp nhập đúng mã: Tiêu thụ thử thách một cách nguyên tử (Atomic Update)
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
    # Trả về kết quả hợp lệ kèm ID người dùng để cấp session
    return "valid", str(consumed["user_id"])


def resend_challenge(secret: str | None) -> tuple[str, str | None, str | None]:
    """Cấp lại mã OTP mới: ghi đè mã 6 số mới vào challenge cũ, gia hạn 5 phút, reset số lần thử về 0 (tối đa 5 lần)."""
    if not secret:
        return "missing", None, None
    challenges = get_collection("auth_challenges")
    key = _challenge_key(secret)
    challenge = challenges.find_one({"challenge_key": key, "used_at": None})
    if not challenge:
        return "missing", None, None
    # Chặn nếu đã gửi lại quá 5 lần trên thử thách này
    if challenge.get("resends", 0) >= MAX_RESENDS:
        return "limit", None, None
    now = _now()
    # Chặn nếu chưa hết thời gian chờ 60 giây giữa 2 lần gửi
    if _aware(challenge["resend_available_at"]) > now:
        return "cooldown", None, None

    # Sinh mã 6 chữ số mới
    code = f"{secrets.randbelow(1_000_000):06d}"
    # Cập nhật nguyên tử vào chính bản ghi hiện tại
    updated = challenges.find_one_and_update(
        {
            "challenge_key": key,
            "used_at": None,
            "resends": {"$lt": MAX_RESENDS},
            "resend_available_at": {"$lte": now},
        },
        {"$set": {
            "otp_hash": _code_hash(secret, code),        # Ghi đè bản băm mã OTP mới
            "created_at": now,                           # Cập nhật thời điểm phát hành
            "expires_at": now + OTP_TTL,                 # Gia hạn thêm 5 phút cho mã mới
            "resend_available_at": now + RESEND_INTERVAL,# Thiết lập lại thời điểm chờ 60 giây tiếp theo
            "attempts": 0,                               # Đặt lại số lần thử sai về 0
        }, "$inc": {"resends": 1}},                      # Tăng bộ đếm số lần gửi lại
        return_document=ReturnDocument.AFTER,
    )
    if not updated:
        return "cooldown", None, None
    return "sent", updated["email"], code


def cancel_challenge(secret: str | None) -> None:
    """Hủy bỏ và xóa hoàn toàn thử thách khỏi MongoDB khi người dùng bấm 'Back to Login'."""
    if secret:
        get_collection("auth_challenges").delete_one({"challenge_key": _challenge_key(secret)})


def allow_immediate_resend(secret: str | None) -> None:
    """Bỏ qua thời gian chờ cooldown 60s (cho phép gửi lại ngay lập tức) nếu gửi email SMTP thất bại."""
    if secret:
        get_collection("auth_challenges").update_one(
            {"challenge_key": _challenge_key(secret), "used_at": None},
            {"$set": {"resend_available_at": _now()}},
        )
