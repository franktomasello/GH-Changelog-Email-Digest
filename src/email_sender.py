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


def _mask_email(email: str) -> str:
    """Mask an address for logging, e.g. 'alice@github.com' -> 'a***@github.com'.

    Keeps the domain (handy for debugging delivery) but not the full identity,
    so recipient addresses never land in (public) CI logs.
    """
    name, sep, domain = email.partition("@")
    if not sep:
        return (name[:1] or "") + "***"
    return (name[:1] or "") + "***@" + domain


def _compact_html(html: str) -> str:
    """Strip template indentation and blank lines from rendered HTML.

    Table layout is whitespace-insensitive, so this is lossless visually but
    cuts ~20% of the payload — keeping busy digests under Gmail's ~102KB
    clipping threshold.
    """
    lines = (line.strip() for line in html.split("\n"))
    return "\n".join(line for line in lines if line)


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
        digest_date = datetime.now(tz=PACIFIC_TZ).strftime("%A, %B %-d, %Y")

    html_content = template.render(
        releases=releases,
        improvements=improvements,
        retirements=retirements,
        digest_date=digest_date,
        total_count=len(releases) + len(improvements) + len(retirements),
    )

    return _compact_html(html_content)


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
                    print(f"  ✓ Email sent to {_mask_email(email)}")
                    success_count += 1
                except Exception as e:
                    print(f"  ✗ Failed to send to {_mask_email(email)}: {e}")
                    failed_recipients.append(email)

    except Exception as e:
        print(f"SMTP connection error: {e}")
        return False

    print(f"Email delivery complete: {success_count}/{len(to_emails)} successful")

    if failed_recipients:
        print(f"Failed recipients: {', '.join(_mask_email(e) for e in failed_recipients)}")

    return success_count > 0


def send_digest_email(
    releases: list[dict],
    improvements: list[dict],
    retirements: list[dict],
    to_emails: Optional[list[str]] = None,
) -> bool:
    """Build and send the changelog digest email."""
    if not to_emails:
        # DIGEST_TEST_EMAIL (a manual test-run override) takes precedence over
        # the normal DIGEST_TO_EMAIL recipient list when it is set.
        raw = os.environ.get("DIGEST_TEST_EMAIL", "").strip() or os.environ.get("DIGEST_TO_EMAIL", "")
        to_emails = [e.strip() for e in raw.split(",") if e.strip()]

    # Log the count only — avoid writing recipient addresses to CI logs.
    print(f"📬 Resolved {len(to_emails)} recipient(s)")

    if not to_emails:
        raise ValueError(
            "No recipients configured. Set the DIGEST_TO_EMAIL environment "
            "variable (comma-separated for multiple), or DIGEST_TEST_EMAIL for "
            "a one-off test send."
        )

    # Build email content
    html_content = build_email_html(releases, improvements, retirements)

    # Build subject line
    total_items = len(releases) + len(improvements) + len(retirements)
    date_str = datetime.now(tz=PACIFIC_TZ).strftime("%a, %b %-d")
    update_word = "update" if total_items == 1 else "updates"
    if total_items == 0:
        subject = f"No changelog updates today · {date_str}"
    else:
        subject = f"{total_items} new {update_word} in the GitHub Changelog · {date_str}"

    # Send email
    return send_email(to_emails, subject, html_content)
