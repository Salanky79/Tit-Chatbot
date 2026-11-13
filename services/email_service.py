"""SMTP delivery for account verification messages."""

from email.message import EmailMessage
import os
import smtplib
import ssl


def send_verification_code(email: str, code: str) -> None:
    """Send a short-lived sign-in verification code using configured SMTP."""
    host = os.getenv("SMTP_HOST")
    username = os.getenv("SMTP_USERNAME") or os.getenv("SMTP_EMAIL")
    password = os.getenv("SMTP_PASSWORD")
    sender = os.getenv("SMTP_FROM", username or "")
    if not host or not sender:
        raise RuntimeError(
            "Email delivery is not configured. Set SMTP_HOST and SMTP_EMAIL "
            "(or SMTP_USERNAME). SMTP_FROM is optional."
        )

    port = int(os.getenv("SMTP_PORT", "587"))
    use_ssl = os.getenv("SMTP_USE_SSL", "false").lower() in {"1", "true", "yes"}
    message = EmailMessage()
    message["Subject"] = "Mã xác minh email | Tit Chatbot"
    message["From"] = sender
    message["To"] = email
    message.set_content(
        f"Xin chào,\n\n"
        f"Mã xác minh Tit Chatbot của bạn là: {code}\n\n"
        "Mã có hiệu lực trong 5 phút và chỉ sử dụng được một lần.\n"
        "Nếu bạn không yêu cầu mã này, vui lòng bỏ qua email.\n\n"
        "Trân trọng,\nĐội ngũ Tit Chatbot"
    )
    message.add_alternative(
        f"""\
<!doctype html>
<html lang="vi">
  <body style="margin:0;padding:0;background:#f3f4f6;font-family:Arial,Helvetica,sans-serif;color:#202124;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f3f4f6;padding:32px 12px;">
      <tr><td align="center">
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:520px;background:#ffffff;border:1px solid #e5e7eb;border-radius:14px;overflow:hidden;">
          <tr><td style="padding:24px 32px;background:#111827;color:#ffffff;">
            <div style="font-size:12px;letter-spacing:2px;text-transform:uppercase;color:#fca5a5;font-weight:700;">TIT CHATBOT</div>
            <div style="font-size:22px;line-height:1.3;font-weight:700;margin-top:8px;">Xác minh địa chỉ email</div>
          </td></tr>
          <tr><td style="padding:30px 32px 16px;font-size:15px;line-height:1.65;">
            <p style="margin:0 0 14px;">Xin chào,</p>
            <p style="margin:0 0 22px;">Dùng mã bên dưới để hoàn tất đăng nhập vào tài khoản Tit Chatbot của bạn:</p>
            <div style="padding:18px 12px;text-align:center;background:#fff7f7;border:1px solid #fecaca;border-radius:10px;color:#b42332;font-size:32px;line-height:1.2;font-weight:700;letter-spacing:10px;">{code}</div>
            <p style="margin:18px 0 0;color:#4b5563;">Mã có hiệu lực trong <strong>5 phút</strong> và chỉ sử dụng được một lần.</p>
            <p style="margin:18px 0 0;color:#6b7280;font-size:13px;">Nếu bạn không yêu cầu mã này, vui lòng bỏ qua email. Tài khoản của bạn vẫn an toàn.</p>
          </td></tr>
          <tr><td style="padding:18px 32px 24px;border-top:1px solid #f0f1f3;color:#6b7280;font-size:12px;line-height:1.5;">
            Email tự động từ Tit Chatbot. Vui lòng không trả lời email này.
          </td></tr>
        </table>
      </td></tr>
    </table>
  </body>
</html>
""",
        subtype="html",
    )

    context = ssl.create_default_context()
    if use_ssl:
        with smtplib.SMTP_SSL(host, port, context=context, timeout=15) as smtp:
            if username and password:
                smtp.login(username, password)
            smtp.send_message(message)
    else:
        with smtplib.SMTP(host, port, timeout=15) as smtp:
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.ehlo()
            if username and password:
                smtp.login(username, password)
            smtp.send_message(message)
