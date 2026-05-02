import os
import logging
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
FROM_EMAIL = os.getenv("FROM_EMAIL", "Avris AI <noreply@theavris.ai>")


def send_password_reset_code(to_email: str, code: str, full_name: str = "") -> bool:
    """Send password reset code via Resend. Falls back to console log if API key missing."""
    subject = "Avris AI — код для сброса пароля"
    greeting = f"Здравствуйте, {full_name}!" if full_name else "Здравствуйте!"
    html = f"""
    <div style="font-family:Inter,Arial,sans-serif;max-width:480px;margin:0 auto;padding:24px;color:#1a202c">
      <h2 style="color:#4AA391;margin:0 0 16px">Avris AI</h2>
      <p>{greeting}</p>
      <p>Вы запросили сброс пароля. Используйте код ниже для подтверждения:</p>
      <div style="font-family:'JetBrains Mono',monospace;font-size:32px;font-weight:700;
                  letter-spacing:6px;text-align:center;background:#F1F0EA;border-radius:8px;
                  padding:18px;margin:20px 0">{code}</div>
      <p style="color:#8a8275;font-size:13px">Код действителен 15 минут.
         Если вы не запрашивали сброс — просто проигнорируйте это письмо.</p>
      <hr style="border:none;border-top:1px solid rgba(0,0,0,.08);margin:24px 0">
      <p style="color:#8a8275;font-size:12px">Hyperion Labs · Avris AI</p>
    </div>
    """

    if not RESEND_API_KEY or RESEND_API_KEY == "your-resend-key":
        logger.warning("RESEND_API_KEY not set — password reset code for %s: %s", to_email, code)
        print(f"[email_service] Reset code for {to_email}: {code}")
        return True

    try:
        import resend
        resend.api_key = RESEND_API_KEY
        resend.Emails.send({
            "from": FROM_EMAIL,
            "to": to_email,
            "subject": subject,
            "html": html,
        })
        return True
    except Exception as e:
        logger.error("Failed to send email via Resend: %s", e)
        print(f"[email_service] Reset code for {to_email}: {code} (Resend failed: {e})")
        return False
