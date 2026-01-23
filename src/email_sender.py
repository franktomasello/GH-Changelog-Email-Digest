"""Email building and sending functionality using SMTP (Gmail or other providers)."""

import os
import smtplib
import ssl
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader

# Pacific Time Zone
PACIFIC_TZ = ZoneInfo("America/Los_Angeles")

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "..", "templates")


def build_email_html(
    releases: list[dict],
    improvements: list[dict],
    retirements: list[dict],
    digest_date: Optional[str] = None,
) -> str:
    """Build the HTML email content using Jinja2 template."""
    env = Environment(loader=FileSystemLoader(TEMPLATES_DIR))
    template = env.get_template("digest_email.html")

    if digest_date is None:
        digest_date = datetime.now(tz=PACIFIC_TZ).strftime("%B %d, %Y")

    html_content = template.render(
        releases=releases,
        improvements=improvements,
        retirements=retirements,
        digest_date=digest_date,
        total_count=len(releases) + len(improvements) + len(retirements),
    )

    return html_content


def send_email(
    to_emails: list[str],
    subject: str,
    html_content: str,
    from_email: Optional[str] = None,
) -> bool:
    """Send an email using SMTP to each recipient individually."""
    smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    from_email = from_email or os.environ.get("SMTP_FROM_EMAIL") or smtp_user

    if not smtp_user or not smtp_password:
        raise ValueError("SMTP_USER and SMTP_PASSWORD environment variables are required")

    # Send individual emails to each recipient for reliable delivery
    success_count = 0
    failed_recipients = []

    # Create secure SSL context
    context = ssl.create_default_context()

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls(context=context)
            server.login(smtp_user, smtp_password)

            for email in to_emails:
                try:
                    # Create message
                    msg = MIMEMultipart("alternative")
                    msg["Subject"] = subject
                    msg["From"] = from_email
                    msg["To"] = email

                    # Attach HTML content
                    msg.attach(MIMEText(html_content, "html"))

                    # Send
                    server.sendmail(from_email, email, msg.as_string())
                    print(f"  ✓ Email sent to {email}")
                    success_count += 1
                except Exception as e:
                    print(f"  ✗ Failed to send to {email}: {e}")
                    failed_recipients.append(email)

    except Exception as e:
        print(f"SMTP connection error: {e}")
        return False

    print(f"Email delivery complete: {success_count}/{len(to_emails)} successful")

    if failed_recipients:
        print(f"Failed recipients: {', '.join(failed_recipients)}")

    return success_count > 0


def send_digest_email(
    releases: list[dict],
    improvements: list[dict],
    retirements: list[dict],
    to_emails: Optional[list[str]] = None,
) -> bool:
    """Build and send the changelog digest email."""
    if not to_emails:
        env_emails = os.environ.get("DIGEST_TO_EMAIL", "")
        print(f"📬 DIGEST_TO_EMAIL raw value: '{env_emails}'")
        to_emails = [e.strip() for e in env_emails.split(",") if e.strip()]
        print(f"📬 Parsed {len(to_emails)} recipient(s): {to_emails}")

    if not to_emails:
        raise ValueError("DIGEST_TO_EMAIL environment variable is required (comma-separated for multiple)")

    # Build email content
    html_content = build_email_html(releases, improvements, retirements)

    # Build subject line
    total_items = len(releases) + len(improvements) + len(retirements)
    date_str = datetime.now(tz=PACIFIC_TZ).strftime("%b %d")
    subject = f"🚀 GitHub Changelog Digest - {date_str} ({total_items} updates)"

    # Send email
    return send_email(to_emails, subject, html_content)
