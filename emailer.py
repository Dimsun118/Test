"""Optional: email the weekly report. Configure with environment variables:

SMTP_HOST, SMTP_PORT (default 587), SMTP_USER, SMTP_PASSWORD, REPORT_TO (comma-separated), REPORT_FROM (optional).
For Gmail use smtp.gmail.com and an App Password (https://myaccount.google.com/apppasswords).
"""
from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from pathlib import Path


def configured() -> bool:
    return all(os.environ.get(k) for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "REPORT_TO"))


def send(report: Path, summary: str, link: str | None = None) -> None:
    msg = EmailMessage()
    msg["Subject"] = summary.splitlines()[0]
    msg["From"] = os.environ.get("REPORT_FROM") or os.environ["SMTP_USER"]
    msg["To"] = os.environ["REPORT_TO"]
    body = summary + (f"\nFull report: {link}\n" if link else "\nThe full interactive report is attached.\n")
    msg.set_content(body)
    msg.add_attachment(report.read_bytes(), maintype="text", subtype="html", filename=report.name)
    with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", "587")), timeout=60) as smtp:
        smtp.starttls()
        smtp.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
        smtp.send_message(msg)
