"""Verification email copy, branding, purpose, and escaping."""
import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

import otp_service  # noqa: E402


def test_registration_email_matches_approved_copy_and_mtb_theme(monkeypatch):
    monkeypatch.setattr(otp_service, "OTP_TTL_MIN", 10)
    monkeypatch.setenv("MTB_SUPPORT_EMAIL", "help@mytrialboard.app")

    subject, text, html = otp_service.build_verification_email(
        code="976559",
        user_name="Asha Rao",
    )

    assert subject == "Your My Trial Board verification code"
    assert "Dear Asha Rao," in text
    assert "Welcome to My Trial Board (MTB)." in text
    assert (
        "Use the verification code below to complete your registration on the "
        "MTB mobile application:"
    ) in text
    assert "976559" in text
    assert "This code is valid for 10 minutes." in text
    assert "do not share this code with anyone" in text
    assert "help@mytrialboard.app" in text
    assert text.endswith("Regards,\nMTB Support Team")

    assert "MTB Verification Code" in html
    assert "background-color:#A6213F" in html
    assert "background-color:#F4E5D3" in html
    assert "background-color:#FEFAF1" in html
    assert "font-size:32px" in html
    assert "margin:24px 0;color:#6B1437" in html
    assert "background-color:#FDE8E1" not in html
    assert "border:1px solid #E7A8B6" not in html
    assert "border-radius:12px" not in html
    assert ">976559</div>" in html
    assert 'href="mailto:help@mytrialboard.app"' in html


def test_email_uses_purpose_correct_copy_and_escapes_dynamic_values(monkeypatch):
    monkeypatch.setattr(otp_service, "OTP_TTL_MIN", 5)

    _, recovery_text, recovery_html = otp_service.build_verification_email(
        code="123456<script>",
        user_name="  Sam\n<script>alert(1)</script>  ",
        purpose="password_recovery",
        support_email='support@example.com" onclick="bad',
    )

    assert "reset your password" in recovery_text
    assert "complete your registration" not in recovery_text
    assert "123456&lt;script&gt;" in recovery_html
    assert "<script>alert(1)</script>" not in recovery_html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in recovery_html
    assert "&quot; onclick=&quot;bad" in recovery_html

    _, contact_text, _ = otp_service.build_verification_email(
        code="654321",
        user_name="Maya",
        purpose="contact_change",
    )
    assert "verify your new email address" in contact_text
    assert "complete your registration" not in contact_text


def test_send_email_passes_rendered_content_to_brevo(monkeypatch):
    sent = {}

    def capture(**payload):
        sent.update(payload)
        return "message-id"

    monkeypatch.setattr(otp_service, "_send_brevo_email", capture)
    otp_service.send_email(
        "asha@example.com",
        "112233",
        "Asha Rao",
        "registration",
    )

    assert sent["to_email"] == "asha@example.com"
    assert "Dear Asha Rao," in sent["text_content"]
    assert ">112233</div>" in sent["html_content"]


def test_invitation_email_keeps_original_ui_with_approved_copy():
    subject, text, html = otp_service.build_invitation_email(
        "https://my-trial-board.app/invite/MTB-9A4A-A6B9",
        "Riya Chowdhary",
        "Dr. Aisha Rao",
        "AIIMS Delhi",
    )

    assert subject == "You're invited to My Trial Board"
    assert "Hi Riya Chowdhary," in text
    assert (
        "You've been invited by Dr. Aisha Rao from AIIMS Delhi to join "
        "My Trial Board."
    ) in text
    assert "MTB-9A4A-A6B9" in text
    assert "MY TRIAL BOARD" in html
    assert "max-width:560px" in html
    assert "background:#F4E5D3" in html
    assert "background:#FEFAF1" in html
    assert "border-radius:18px" in html
    assert "background:#FDE8E1" in html
    assert "border:1px dashed #A6213F" in html
    assert "border-radius:12px" in html
    assert "<strong>Dr. Aisha Rao</strong>" in html
    assert "<strong>AIIMS Delhi</strong>" in html
    assert (
        "You've been invited by <strong>Dr. Aisha Rao</strong> from "
        "<strong>AIIMS Delhi</strong> to join My Trial Board."
    ) in html
    assert ">MTB-9A4A-A6B9</div>" in html


def test_invitation_email_escapes_personalized_values_and_reaches_brevo(monkeypatch):
    sent = {}

    def capture(**payload):
        sent.update(payload)
        return "message-id"

    monkeypatch.setattr(otp_service, "_send_brevo_email", capture)
    otp_service.send_invitation_email(
        "riya@example.com",
        "https://my-trial-board.app/invite/MTB-1234-5678",
        "Riya <Admin>",
        "Aisha <script>",
        "MTB & Partners",
    )

    assert sent["to_email"] == "riya@example.com"
    assert "Aisha <script> from MTB & Partners" in sent["text_content"]
    assert "Aisha &lt;script&gt;" in sent["html_content"]
    assert "MTB &amp; Partners" in sent["html_content"]
    assert "<script>" not in sent["html_content"]
