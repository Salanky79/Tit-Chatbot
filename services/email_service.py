"""SMTP delivery for account verification and project invitations."""

from datetime import datetime
from email.message import EmailMessage
from html import escape
import os
import smtplib
import ssl


def send_verification_code(email: str, code: str) -> None:
    """Send a short-lived sign-in verification code using configured SMTP."""
    message = EmailMessage()
    message["Subject"] = "Mã xác minh email | Tit Chatbot"
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

    _send_message(message)


def send_project_invitation(email: str, project_name: str, inviter_name: str, role: str, invitation_url: str, expires_at: str) -> None:
    """Send a recipient-bound link; SMTP acceptance is reported separately by the API."""
    role_name = "Có thể chỉnh sửa" if role == "editor" else "Chỉ trò chuyện"
    deadline = datetime.fromisoformat(expires_at).strftime("%d/%m/%Y %H:%M UTC")
    message = EmailMessage()
    message["Subject"] = "Lời mời tham gia Project | Tit Chatbot"
    message["To"] = email
    message.set_content(
        f"{inviter_name} mời bạn tham gia Project {project_name} trên Tit Chatbot.\n\n"
        f"Quyền: {role_name}\nHết hạn: {deadline}\n\n{invitation_url}\n\n"
        "Hãy đăng nhập đúng tài khoản nhận lời mời, sau đó chọn Chấp nhận hoặc Từ chối. "
        "Bạn cũng có thể tìm lời mời trong mục Project của ứng dụng.\n"
        "Nếu không mong đợi lời mời này, bạn có thể bỏ qua email."
    )
    message.add_alternative(f"""<!doctype html><html lang="vi"><body style="font-family:Arial,sans-serif;background:#f8f4f5;color:#241519;padding:24px;">
        <div style="max-width:520px;margin:auto;background:white;padding:28px;border-radius:14px;">
        <h2>Lời mời tham gia Project</h2>
        <p><strong>{escape(inviter_name)}</strong> mời bạn tham gia <strong>{escape(project_name)}</strong>.</p>
        <p>Quyền: {escape(role_name)}<br>Hết hạn: {escape(deadline)}</p>
        <p style="margin:28px 0;"><a href="{escape(invitation_url, quote=True)}" style="display:inline-block;background:#c92038;color:white;padding:12px 20px;border-radius:8px;text-decoration:none;">Xem lời mời</a></p>
        <p>Đăng nhập đúng tài khoản nhận lời mời để chấp nhận hoặc từ chối.</p>
        <p style="font-size:12px;color:#665;"><a href="{escape(invitation_url, quote=True)}">Mở link mời</a> · Bạn cũng có thể xem lời mời trong mục Project của ứng dụng.</p>
        </div></body></html>""", subtype="html")
    _send_message(message)


def _send_message(message: EmailMessage) -> None:
    host = os.getenv("SMTP_HOST")
    username = os.getenv("SMTP_USERNAME") or os.getenv("SMTP_EMAIL")
    password = os.getenv("SMTP_PASSWORD")
    sender = os.getenv("SMTP_FROM", username or "")
    if not host or not sender:
        raise RuntimeError("Email delivery is not configured. Set SMTP_HOST and SMTP_EMAIL (or SMTP_USERNAME). SMTP_FROM is optional.")
    port = int(os.getenv("SMTP_PORT", "587"))
    use_ssl = os.getenv("SMTP_USE_SSL", "false").lower() in {"1", "true", "yes"}
    message["From"] = sender
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
