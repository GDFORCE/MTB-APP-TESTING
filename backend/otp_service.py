"""OTP delivery — production providers.

SMS  : MSG91 (India). Requires a DLT-approved OTP template.
Email: Brevo Transactional Email API.

There is no demo / fallback path: if a channel is requested but not configured,
or the provider rejects the request, we raise so the caller fails loudly instead
of silently telling the user a code was sent. Codes are never logged.
"""
import os
import logging
import secrets
from html import escape

import requests

log = logging.getLogger("otp")

OTP_TTL_MIN = int(os.environ.get("OTP_TTL_MIN", "10"))


class OTPConfigError(RuntimeError):
    """A channel was requested but its provider isn't configured."""


class OTPDeliveryError(RuntimeError):
    """The provider accepted the request path but failed to deliver."""


def generate_code(length: int = 6) -> str:
    """Cryptographically-random numeric OTP."""
    return "".join(str(secrets.randbelow(10)) for _ in range(length))


def _require(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise OTPConfigError(f"{name} is not configured")
    return val


# ── SMS via MSG91 ─────────────────────────────────────────────────────────────
def send_sms(phone: str, code: str) -> None:
    """Send `code` to `phone` (E.164, e.g. +9198…) via MSG91. Raises on failure."""
    authkey = _require("MSG91_AUTHKEY")
    template_id = _require("MSG91_TEMPLATE_ID")
    mobile = phone.lstrip("+").replace(" ", "")
    try:
        r = requests.post(
            "https://control.msg91.com/api/v5/otp",
            params={"template_id": template_id, "mobile": mobile, "otp": code},
            headers={"authkey": authkey, "Content-Type": "application/json"},
            timeout=15,
        )
    except requests.RequestException as e:
        log.error("MSG91 request failed for %s: %s", mobile, e)
        raise OTPDeliveryError("Could not reach the SMS provider") from e

    ok = r.status_code == 200
    data = {}
    try:
        data = r.json()
    except ValueError:
        ok = False
    if not ok or str(data.get("type", "")).lower() == "error":
        log.error("MSG91 rejected SMS to %s: %s %s", mobile, r.status_code, data or r.text)
        raise OTPDeliveryError("The SMS provider rejected the request")


# ── Email via Brevo Transactional Email API ───────────────────────────────────
BREVO_EMAIL_URL = "https://api.brevo.com/v3/smtp/email"


def _send_brevo_email(*, to_email: str, subject: str, text_content: str, html_content: str) -> str:
    """Send one transactional email through Brevo and return its message ID."""
    api_key = _require("BREVO_API_KEY")
    sender_email = _require("BREVO_FROM_EMAIL")
    sender_name = os.environ.get("BREVO_FROM_NAME", "My Trial Board")
    payload = {
        "sender": {"email": sender_email, "name": sender_name},
        "to": [{"email": to_email}],
        "subject": subject,
        "textContent": text_content,
        "htmlContent": html_content,
        "tags": ["mtb-transactional"],
    }
    try:
        response = requests.post(
            BREVO_EMAIL_URL,
            headers={
                "api-key": api_key,
                "accept": "application/json",
                "content-type": "application/json",
            },
            json=payload,
            timeout=20,
        )
        response.raise_for_status()
        return response.json()["messageId"]
    except (requests.RequestException, KeyError, ValueError) as exc:
        log.error("Brevo email send to %s failed: %s", to_email, exc)
        raise OTPDeliveryError("Could not send the email") from exc


def _single_line(value: str, fallback: str) -> str:
    """Keep user/config values from breaking the text or HTML email layout."""
    return " ".join((value or "").split()) or fallback


def build_verification_email(
    code: str,
    user_name: str = "",
    purpose: str = "registration",
    support_email: str = "",
) -> tuple[str, str, str]:
    """Return the subject, plain text, and branded HTML for an OTP email."""
    name = _single_line(user_name, "User")
    verification_code = _single_line(code, "")
    support = _single_line(
        support_email or os.environ.get("MTB_SUPPORT_EMAIL", ""),
        "support@mytrialboard.app",
    )

    if purpose == "password_recovery":
        introduction = (
            "Use the verification code below to reset your password on the "
            "MTB mobile application:"
        )
        welcome = "We received a request to reset your My Trial Board (MTB) password."
    elif purpose == "login_support":
        introduction = (
            "Use the verification code below to confirm your registered email and "
            "submit your login support ticket:"
        )
        welcome = "We received a request for help signing in to My Trial Board (MTB)."
    elif purpose == "contact_change":
        introduction = (
            "Use the verification code below to verify your new email address on the "
            "MTB mobile application:"
        )
        welcome = "We received a request to change your My Trial Board (MTB) email address."
    else:
        introduction = (
            "Use the verification code below to complete your registration on the "
            "MTB mobile application:"
        )
        welcome = "Welcome to My Trial Board (MTB)."

    subject = "Your My Trial Board verification code"
    text_content = (
        f"Dear {name},\n\n"
        f"{welcome}\n\n"
        f"{introduction}\n\n"
        f"{verification_code}\n\n"
        f"This code is valid for {OTP_TTL_MIN} minutes. For your security, do not share "
        "this code with anyone.\n\n"
        "If you did not request this code, please ignore this email or contact our "
        f"support team at {support}.\n\n"
        "Regards,\n"
        "MTB Support Team"
    )

    safe_name = escape(name)
    safe_code = escape(verification_code)
    safe_support = escape(support, quote=True)
    safe_welcome = escape(welcome)
    safe_introduction = escape(introduction)
    html_content = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>MTB Verification Code</title>
  </head>
  <body style="margin:0;padding:0;background-color:#F4E5D3;color:#2E1B33;font-family:Arial,Helvetica,sans-serif;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="width:100%;background-color:#F4E5D3;">
      <tr>
        <td align="center" style="padding:32px 16px;">
          <table role="presentation" width="600" cellspacing="0" cellpadding="0" border="0" style="width:100%;max-width:600px;background-color:#FEFAF1;border:1px solid #E6D6C5;border-radius:16px;overflow:hidden;">
            <tr>
              <td style="padding:22px 28px;background-color:#A6213F;color:#FFFFFF;">
                <div style="font-size:12px;font-weight:700;letter-spacing:1.6px;line-height:18px;">MY TRIAL BOARD</div>
                <div style="margin-top:5px;font-size:24px;font-weight:700;line-height:32px;">Verification Code</div>
              </td>
            </tr>
            <tr>
              <td style="padding:30px 28px 32px;">
                <p style="margin:0 0 20px;font-size:16px;line-height:24px;">Dear {safe_name},</p>
                <p style="margin:0 0 20px;font-size:16px;line-height:24px;">{safe_welcome}</p>
                <p style="margin:0 0 20px;font-size:16px;line-height:24px;">{safe_introduction}</p>
                <div style="margin:24px 0;color:#6B1437;font-size:32px;font-weight:700;letter-spacing:7px;line-height:40px;text-align:center;">{safe_code}</div>
                <p style="margin:0 0 20px;font-size:15px;line-height:23px;">This code is valid for <strong>{OTP_TTL_MIN} minutes</strong>. For your security, do not share this code with anyone.</p>
                <p style="margin:0 0 26px;font-size:15px;line-height:23px;color:#5F4A59;">If you did not request this code, please ignore this email or contact our support team at <a href="mailto:{safe_support}" style="color:#A6213F;font-weight:700;text-decoration:none;">{safe_support}</a>.</p>
                <p style="margin:0;font-size:15px;line-height:23px;">Regards,<br><strong>MTB Support Team</strong></p>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>"""
    return subject, text_content, html_content


def send_email(
    email: str,
    code: str,
    user_name: str = "",
    purpose: str = "registration",
) -> None:
    """Send a purpose-correct branded verification email through Brevo."""
    subject, text_content, html_content = build_verification_email(
        code=code,
        user_name=user_name,
        purpose=purpose,
    )
    _send_brevo_email(
        to_email=email,
        subject=subject,
        text_content=text_content,
        html_content=html_content,
    )


def build_invitation_email(
    invite_link: str,
    recipient_name: str = "",
    inviter_name: str = "",
    organization_name: str = "",
) -> tuple[str, str, str]:
    """Return the subject, plain text, and original MTB-card invite email."""
    recipient = _single_line(recipient_name, "there")
    inviter = _single_line(inviter_name, "the MTB team")
    organization = _single_line(organization_name, "My Trial Board")
    invite_code = invite_link.rstrip("/").rsplit("/", 1)[-1].split("?", 1)[0]
    safe_recipient = escape(recipient)
    safe_inviter = escape(inviter)
    safe_organization = escape(organization)
    safe_code = escape(invite_code)
    subject = "You're invited to My Trial Board"
    text_content = (
        f"Hi {recipient},\n\n"
        f"You've been invited by {inviter} from {organization} to join My Trial Board.\n\n"
        "Open the app, select Join with an invite, then enter this code:\n\n"
        f"{invite_code}\n\n"
        "This invitation expires in 3 days. If you did not expect it, you can safely ignore this email."
    )
    html_content = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>My Trial Board Invitation</title>
  </head>
  <body style="margin:0;padding:0;background-color:#F4E5D3;">
    <div style="margin:0;padding:28px 16px;background:#F4E5D3;font-family:Arial,sans-serif;color:#2E1B33;">
      <div style="max-width:560px;margin:auto;background:#FEFAF1;border-radius:18px;border:1px solid #E6D6C5;padding:28px;">
        <div style="color:#A6213F;font-size:13px;font-weight:700;letter-spacing:1px;">MY TRIAL BOARD</div>
        <h1 style="margin:12px 0 8px;font-size:24px;color:#2E1B33;">Hi {safe_recipient},</h1>
        <p style="font-size:16px;line-height:24px;">You've been invited by <strong>{safe_inviter}</strong> from <strong>{safe_organization}</strong> to join My Trial Board.</p>
        <p style="font-size:14px;line-height:21px;">Open the app, select <strong>Join with an invite</strong>, then enter this code:</p>
        <div style="margin:20px 0;padding:16px;background:#FDE8E1;border:1px dashed #A6213F;border-radius:12px;color:#A6213F;font-size:22px;font-weight:700;letter-spacing:2px;text-align:center;">{safe_code}</div>
        <p style="font-size:14px;line-height:21px;color:#7B5F73;">This invitation expires in 3 days. If you did not expect it, you can safely ignore this email.</p>
      </div>
    </div>
  </body>
</html>"""
    return subject, text_content, html_content


def send_invitation_email(
    email: str,
    invite_link: str,
    recipient_name: str = "",
    inviter_name: str = "",
    organization_name: str = "",
) -> None:
    """Deliver a personalized, time-limited My Trial Board invitation code."""
    subject, text_content, html_content = build_invitation_email(
        invite_link,
        recipient_name,
        inviter_name,
        organization_name,
    )
    _send_brevo_email(
        to_email=email,
        subject=subject,
        text_content=text_content,
        html_content=html_content,
    )


def send_password_reset_email(email: str, reset_link: str, expires_minutes: int) -> None:
    """Deliver a single-use password setup/reset link without exposing it to admins."""
    _send_brevo_email(
        to_email=email,
        subject="Set your My Trial Board password",
        text_content=(
            "A My Trial Board administrator requested a password setup or reset for your account.\n\n"
            f"Open this single-use link within {expires_minutes} minutes:\n{reset_link}\n\n"
            "If you did not expect this message, contact support. Do not share this link."
        ),
        html_content=(
            "<p>A My Trial Board administrator requested a password setup or reset for your account.</p>"
            f"<p><a href=\"{reset_link}\">Set your password</a></p>"
            f"<p>This single-use link expires in {expires_minutes} minutes.</p>"
            "<p>If you did not expect this message, contact support. Do not share this link.</p>"
        ),
    )
