from __future__ import annotations

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path


def send_email(
    html_path: str,
    subject: str,
    recipients: list[str],
    *,
    smtp_host: str = "smtp.gmail.com",
    smtp_port: int = 587,
    use_tls: bool = True,
) -> None:
    email_user = os.getenv("EMAIL_USER")
    email_password = os.getenv("EMAIL_PASSWORD")

    if not email_user or not email_password:
        raise RuntimeError("EMAIL_USER e EMAIL_PASSWORD devem estar definidos no ambiente.")

    html_content = Path(html_path).read_text(encoding="utf-8")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = email_user
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(html_content, "html", "utf-8"))

    with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
        if use_tls:
            server.starttls()
        server.login(email_user, email_password)
        server.sendmail(email_user, recipients, msg.as_string())

