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


def _render_plain(headline: str, body_html: str, full_name: str = "") -> str:
    greeting = f"Здравствуйте, {full_name}!" if full_name else "Здравствуйте!"
    return f"""
    <div style="font-family:Inter,Arial,sans-serif;max-width:520px;margin:0 auto;padding:24px;color:#1a202c">
      <h2 style="color:#4AA391;margin:0 0 16px">Avris AI</h2>
      <p>{greeting}</p>
      <p><strong>{headline}</strong></p>
      {body_html}
      <hr style="border:none;border-top:1px solid rgba(0,0,0,.08);margin:24px 0">
      <p style="color:#8a8275;font-size:12px">Hyperion Labs · Avris AI</p>
    </div>
    """


def send_admin_new_doctor_alert(admin_email: str, doctor_full_name: str, doctor_email: str,
                                  specialty: str = "", hospital: str = "") -> bool:
    body = f"""
      <p>Поступила новая заявка на доступ:</p>
      <ul style="line-height:1.8">
        <li><strong>Имя:</strong> {doctor_full_name}</li>
        <li><strong>Email:</strong> {doctor_email}</li>
        <li><strong>Специальность:</strong> {specialty or '—'}</li>
        <li><strong>Больница:</strong> {hospital or '—'}</li>
      </ul>
      <p style="margin-top:16px">Откройте админ-панель в Avris AI, чтобы одобрить или отклонить заявку.</p>
    """
    html = _render_plain("Новая заявка на доступ", body)
    return _send_via_resend(admin_email, "Avris AI — новая заявка от врача", html, "Admin alert", doctor_email)


def send_doctor_approved(to_email: str, full_name: str = "") -> bool:
    body = """
      <p>Ваша заявка на доступ к Avris AI одобрена.</p>
      <p>Теперь вы можете войти в систему и начать работу.</p>
      <p style="margin-top:18px"><a href="https://theavris.ai" style="background:#4AA391;color:#fff;text-decoration:none;padding:10px 20px;border-radius:8px;font-weight:600;display:inline-block">Войти в Avris AI</a></p>
    """
    html = _render_plain("Добро пожаловать в Avris AI", body, full_name)
    return _send_via_resend(to_email, "Avris AI — ваш доступ одобрен", html, "Approval", to_email)


def send_doctor_rejected(to_email: str, full_name: str = "", reason: str = "") -> bool:
    reason_block = f"<p><strong>Причина:</strong> {reason}</p>" if reason else ""
    body = f"""
      <p>К сожалению, ваша заявка на доступ к Avris AI отклонена.</p>
      {reason_block}
      <p>Если вы считаете это ошибкой, пожалуйста, свяжитесь с нами: <a href="mailto:info@theavris.ai">info@theavris.ai</a></p>
    """
    html = _render_plain("Заявка не одобрена", body, full_name)
    return _send_via_resend(to_email, "Avris AI — заявка отклонена", html, "Rejection", to_email)
