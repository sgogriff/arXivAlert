"""SMTP email composition and delivery."""

import logging
import smtplib
from datetime import datetime
from pathlib import Path
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from src.config import SmtpConfig

logger = logging.getLogger("arXivAlert.mailer")


def _get_logo_path() -> Path:
    here = Path(__file__).resolve().parent.parent
    return here / "templates" / "logo.png"


def send_digest(
    html_content: str,
    plain_text: str,
    date_range: tuple[datetime, datetime],
    smtp_config: SmtpConfig,
) -> bool:
    """Send the digest email via SMTP with TLS.

    Returns True on success, False on failure (logged).
    """
    if not smtp_config.user or not smtp_config.password or not smtp_config.recipient:
        logger.error("SMTP credentials incomplete — set SMTP_USER, SMTP_PASSWORD, and RECIPIENT_EMAIL in .env")
        return False

    start_str = date_range[0].strftime("%Y-%m-%d")
    end_str = date_range[1].strftime("%Y-%m-%d")

    # multipart/related -> multipart/alternative -> (text/plain, text/html)
    # plus inline assets (logo).
    msg = MIMEMultipart("related")
    msg["Subject"] = f"[arXivAlert] Digest: {start_str} to {end_str}"
    msg["From"] = smtp_config.user
    msg["To"] = smtp_config.recipient

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(plain_text, "plain", "utf-8"))
    alt.attach(MIMEText(html_content, "html", "utf-8"))
    msg.attach(alt)

    # Inline logo referenced by cid:arxivalert-logo
    try:
        logo_path = _get_logo_path()
        if logo_path.exists():
            img = MIMEImage(logo_path.read_bytes())
            img.add_header("Content-ID", "<arxivalert-logo>")
            img.add_header("Content-Disposition", "inline", filename="logo.png")
            msg.attach(img)
        else:
            logger.warning("Logo not found at %s; sending email without inline image", logo_path)
    except OSError as e:
        logger.warning("Failed to attach logo image; sending without it: %s", e)

    try:
        with smtplib.SMTP(smtp_config.host, smtp_config.port) as server:
            server.starttls()
            server.login(smtp_config.user, smtp_config.password)
            server.send_message(msg)
        logger.info("Digest emailed to %s", smtp_config.recipient)
        return True
    except smtplib.SMTPException as e:
        logger.error("Failed to send email: %s", e)
        return False
