"""Email building and sending functionality using SMTP (Gmail or other providers)."""

import os
import smtplib
import ssl
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid
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
    env = Environment(loader=FileSystemLoader(TEMPLATES_DIR), autoescape=True)
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

    html_content = _compact_html(html_content)
    size_kb = len(html_content.encode("utf-8")) / 1024
    if size_kb > 100:
        print(f"⚠️  Rendered email is {size_kb:.0f}KB — Gmail clips at ~102KB. "
              "Large sends (e.g. --all) may be truncated for Gmail recipients.")
    return html_content


def build_email_text(
    releases: list[dict],
    improvements: list[dict],
    retirements: list[dict],
    digest_date: Optional[str] = None,
) -> str:
    """Build a plain-text alternative of the digest.

    A multipart/alternative message that carries only a text/html part is a
    known spam signal (SpamAssassin MIME_HTML_ONLY) and leaves text-only and
    accessibility clients with no readable body. This renders the same data as
    plain text so a text/plain part can be attached alongside the HTML.
    """
    if digest_date is None:
        digest_date = datetime.now(tz=PACIFIC_TZ).strftime("%A, %B %-d, %Y")

    total = len(releases) + len(improvements) + len(retirements)
    word = "update" if total == 1 else "updates"

    lines = [
        "GitHub Changelog Digest",
        digest_date,
        "",
        f"{total} new {word} in the GitHub Changelog." if total
        else "No new updates today.",
    ]

    sections = [
        ("RELEASES", releases),
        ("IMPROVEMENTS", improvements),
        ("RETIREMENTS", retirements),
    ]
    for heading, items in sections:
        if not items:
            continue
        lines += ["", f"== {heading} ({len(items)}) =="]
        for it in items:
            lines += ["", f"* {it.get('title', '').strip()}"]
            if it.get("published"):
                lines.append(f"  {it['published']}")
            if it.get("summary"):
                lines.append(f"  {it['summary'].strip()}")
            for feature in it.get("key_features", []) or []:
                lines.append(f"    - {feature}")
            if it.get("docs_url"):
                lines.append(f"  Docs: {it['docs_url']}")
            if it.get("url"):
                lines.append(f"  Link: {it['url']}")

    lines += [
        "",
        "—",
        "Full changelog: https://github.blog/changelog/",
    ]
    return "\n".join(lines) + "\n"


def send_email(
    to_emails: list[str],
    subject: str,
    html_content: str,
    from_email: Optional[str] = None,
    text_content: Optional[str] = None,
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
        # timeout bounds a hung/half-open SMTP endpoint; without it the socket
        # inherits the global default (None = block forever) and the only
        # backstop is the workflow's 5-minute job kill.
        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
            server.starttls(context=context)
            server.login(smtp_user, smtp_password)

            sender_domain = from_email.split("@")[-1] if "@" in (from_email or "") else None
            for email in to_emails:
                try:
                    # Create message
                    msg = MIMEMultipart("alternative")
                    msg["Subject"] = subject
                    msg["From"] = from_email
                    msg["To"] = email
                    # smtplib.sendmail() transmits the message verbatim and adds
                    # no headers, so set Date and a unique Message-ID ourselves —
                    # their absence is a spam signal (e.g. MISSING_MID) and can
                    # cause client threading oddities. make_msgid() is unique
                    # even when called back-to-back, so each copy gets its own ID.
                    msg["Date"] = formatdate(localtime=True)
                    msg["Message-ID"] = make_msgid(domain=sender_domain)
                    # A mailto-based unsubscribe; the maintainer processes opt-outs
                    # by editing the DIGEST_TO_EMAIL secret. (No List-Unsubscribe-Post
                    # / One-Click: that requires an authenticated https endpoint.)
                    msg["List-Unsubscribe"] = f"<mailto:{from_email}?subject=unsubscribe>"

                    # multipart/alternative is least-rich-first: attach plain
                    # text before HTML so text-only clients pick it up.
                    if text_content:
                        msg.attach(MIMEText(text_content, "plain", "utf-8"))
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
        # A partial failure must NOT be reported as success. If it were, main.py
        # would mark every entry as sent and persist state, so the recipients
        # whose send failed would never receive those entries again. Returning
        # False keeps state un-advanced: the run fails loudly (red workflow +
        # ::error:: annotation) and the next run retries for everyone. A few
        # duplicates to already-delivered recipients is far better than a silent
        # permanent drop, and the loud failure flags a persistently-bad address.
        print("::error::Some recipients failed — not advancing state; "
              "entries will be retried on the next run.")
        return False

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

    # Build email content (shared digest date keeps the HTML and text parts in
    # sync) — HTML plus a plain-text alternative for deliverability/accessibility.
    digest_date = datetime.now(tz=PACIFIC_TZ).strftime("%A, %B %-d, %Y")
    html_content = build_email_html(releases, improvements, retirements, digest_date)
    text_content = build_email_text(releases, improvements, retirements, digest_date)

    # Build subject line
    total_items = len(releases) + len(improvements) + len(retirements)
    date_str = datetime.now(tz=PACIFIC_TZ).strftime("%a, %b %-d")
    update_word = "update" if total_items == 1 else "updates"
    if total_items == 0:
        subject = f"No changelog updates today · {date_str}"
    else:
        subject = f"{total_items} new {update_word} in the GitHub Changelog · {date_str}"

    # Send email
    return send_email(to_emails, subject, html_content, text_content=text_content)
