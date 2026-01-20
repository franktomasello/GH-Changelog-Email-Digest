"""
Email building and sending functionality using Resend (free tier: 3,000 emails/month).
"""

import os
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

# Pacific Time Zone
PACIFIC_TZ = ZoneInfo("America/Los_Angeles")

import resend
from jinja2 import Environment, FileSystemLoader

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
        digest_date = datetime.now(tz=PACIFIC_TZ).strftime("%B %d, %Y") + " PT"

    html_content = template.render(
        releases=releases,
        improvements=improvements,
        retirements=retirements,
        digest_date=digest_date,
        total_count=len(releases) + len(improvements) + len(retirements),
    )

    return html_content


def send_email(
    to_email: str,
    subject: str,
    html_content: str,
    from_email: Optional[str] = None,
    api_key: Optional[str] = None,
) -> bool:
    """Send an email using Resend API."""
    api_key = api_key or os.environ.get("RESEND_API_KEY")
    from_email = from_email or os.environ.get("RESEND_FROM_EMAIL", "onboarding@resend.dev")

    if not api_key:
        raise ValueError("RESEND_API_KEY environment variable is required")

    resend.api_key = api_key

    try:
        response = resend.Emails.send({
            "from": from_email,
            "to": [to_email],
            "subject": subject,
            "html": html_content,
        })
        print(f"Email sent successfully. ID: {response['id']}")
        return True
    except Exception as e:
        print(f"Error sending email: {e}")
        return False


def send_digest_email(
    releases: list[dict],
    improvements: list[dict],
    retirements: list[dict],
    to_email: Optional[str] = None,
) -> bool:
    """Build and send the changelog digest email."""
    to_email = to_email or os.environ.get("DIGEST_TO_EMAIL")

    if not to_email:
        raise ValueError("DIGEST_TO_EMAIL environment variable is required")

    # Build email content
    html_content = build_email_html(releases, improvements, retirements)

    # Build subject line
    total_items = len(releases) + len(improvements) + len(retirements)
    date_str = datetime.now(tz=PACIFIC_TZ).strftime("%b %d")
    subject = f"🚀 GitHub Changelog Digest - {date_str} ({total_items} updates)"

    # Send email
    return send_email(to_email, subject, html_content)
