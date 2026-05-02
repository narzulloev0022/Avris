import os
import logging
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
FROM_EMAIL = os.getenv("FROM_EMAIL", "Avris AI <noreply@theavris.ai>")


def _render_code_email(code: str, headline: str, body: str, full_name: str = "") -> str:
    greeting = f"Здравствуйте, {full_name}!" if full_name else "Здравствуйте!"
    return f"""
    <div style="font-family:Inter,Arial,sans-serif;max-width:480px;margin:0 auto;padding:24px;color:#1a202c">
      <h2 style="color:#4AA391;margin:0 0 16px">Avris AI</h2>
      <p>{greeting}</p>
      <p><strong>{headline}</strong></p>
      <p>{body}</p>
      <div style="font-family:'JetBrains Mono',monospace;font-size:32px;font-weight:700;
                  letter-spacing:6px;text-align:center;background:#F1F0EA;border-radius:8px;
                  padding:18px;margin:20px 0">{code}</div>
      <p style="color:#8a8275;font-size:13px">Код действителен 15 минут.
         Если вы не запрашивали этот код — просто проигнорируйте письмо.</p>
      <hr style="border:none;border-top:1px solid rgba(0,0,0,.08);margin:24px 0">
      <p style="color:#8a8275;font-size:12px">Hyperion Labs · Avris AI</p>
    </div>
    """


def _send_via_resend(to_email: str, subject: str, html: str, console_label: str, code: str) -> bool:
    if not RESEND_API_KEY or RESEND_API_KEY == "your-resend-key":
        logger.warning("RESEND_API_KEY not set — %s for %s: %s", console_label, to_email, code)
        print(f"[email_service] {console_label} for {to_email}: {code}")
        return True
    try:
        import resend
        resend.api_key = RESEND_API_KEY
        resend.Emails.send({"from": FROM_EMAIL, "to": to_email, "subject": subject, "html": html})
        return True
    except Exception as e:
        logger.error("Failed to send email via Resend: %s", e)
        print(f"[email_service] {console_label} for {to_email}: {code} (Resend failed: {e})")
        return False


def send_password_reset_code(to_email: str, code: str, full_name: str = "") -> bool:
    html = _render_code_email(
        code,
        "Сброс пароля",
        "Вы запросили сброс пароля. Используйте код ниже для подтверждения:",
        full_name,
    )
    return _send_via_resend(to_email, "Avris AI — код для сброса пароля", html, "Reset code", code)


def send_verification_code(to_email: str, code: str, full_name: str = "") -> bool:
    html = _render_code_email(
        code,
        "Подтверждение email",
        "Введите код ниже, чтобы подтвердить ваш email и завершить регистрацию:",
        full_name,
    )
    return _send_via_resend(to_email, "Avris AI — подтверждение email", html, "Verification code", code)
