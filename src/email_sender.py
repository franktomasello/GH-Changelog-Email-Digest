"""
Email building and sending functionality using SendGrid.
"""

import os
from datetime import datetime
from typing import Optional

from jinja2 import Environment, FileSystemLoader
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, Email, To, Content

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
        digest_date = datetime.now().strftime("%B %d, %Y")

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
    """Send an email using SendGrid API."""
    api_key = api_key or os.environ.get("SENDGRID_API_KEY")
    from_email = from_email or os.environ.get("SENDGRID_FROM_EMAIL")

    if not api_key:
        raise ValueError("SENDGRID_API_KEY environment variable is required")
    if not from_email:
        raise ValueError("SENDGRID_FROM_EMAIL environment variable is required")

    message = Mail(
        from_email=Email(from_email),
        to_emails=To(to_email),
        subject=subject,
        html_content=Content("text/html", html_content),
    )

    try:
        sg = SendGridAPIClient(api_key)
        response = sg.send(message)
        print(f"Email sent successfully. Status code: {response.status_code}")
        return response.status_code in (200, 201, 202)
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
    date_str = datetime.now().strftime("%b %d")
    subject = f"🚀 GitHub Changelog Digest - {date_str} ({total_items} updates)"

    # Send email
    return send_email(to_email, subject, html_content)
