"""Email building and sending: the Brevo HTTP API (which supports scheduled
delivery) when BREVO_API_KEY is set, otherwise plain SMTP (immediate only)."""

import os
import smtplib
import ssl
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid
from typing import Optional
from zoneinfo import ZoneInfo

import requests
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
    # Optional display name and Reply-To. Sending through a relay means the From
    # address must be one that relay has verified, which is not necessarily the
    # mailbox replies should land in. Reply-To carries that second address
    # without putting an unaligned domain in From (which DMARC would punish).
    from_name = os.environ.get("SMTP_FROM_NAME", "").strip()
    reply_to = os.environ.get("SMTP_REPLY_TO", "").strip()

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
            # from_email stays the bare address — it is the SMTP envelope sender,
            # the Message-ID domain and the List-Unsubscribe mailto. Only the
            # From *header* gets the display name, via formataddr so a name
            # containing a comma or non-ASCII is quoted/encoded correctly.
            from_header = formataddr((from_name, from_email)) if from_name else from_email
            for email in to_emails:
                try:
                    # Create message
                    msg = MIMEMultipart("alternative")
                    msg["Subject"] = subject
                    msg["From"] = from_header
                    msg["To"] = email
                    if reply_to:
                        msg["Reply-To"] = reply_to
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


BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"
BREVO_MAX_RECIPIENTS = 2000  # per request, per Brevo's API limits


def send_email_brevo_api(
    to_emails: list[str],
    subject: str,
    html_content: str,
    text_content: Optional[str] = None,
    scheduled_at: Optional[datetime] = None,
    batch_id: Optional[str] = None,
) -> bool:
    """Send (or schedule) via Brevo's transactional API.

    SMTP has no way to say "deliver at 06:00", so scheduled delivery needs
    the API. Uses the same SMTP_FROM_EMAIL / SMTP_FROM_NAME / SMTP_REPLY_TO
    settings as the SMTP path; the From address must be a Brevo-verified
    sender.
    """
    api_key = os.environ.get("BREVO_API_KEY", "").strip()
    from_email = os.environ.get("SMTP_FROM_EMAIL", "").strip()
    from_name = os.environ.get("SMTP_FROM_NAME", "").strip()
    reply_to = os.environ.get("SMTP_REPLY_TO", "").strip()

    if not api_key:
        raise ValueError("BREVO_API_KEY is required for API sending")
    if not from_email:
        # No fallback to SMTP_USER: for Brevo that's a relay login
        # (...@smtp-brevo.com), not a deliverable sender.
        raise ValueError("SMTP_FROM_EMAIL is required (a Brevo-verified sender)")
    if len(to_emails) > BREVO_MAX_RECIPIENTS:
        raise ValueError(f"Brevo accepts at most {BREVO_MAX_RECIPIENTS} recipients per request")

    sender = {"email": from_email}
    if from_name:
        sender["name"] = from_name
    payload = {
        "sender": sender,
        "subject": subject,
        "htmlContent": html_content,
        # One version per recipient, so each person gets an individual email
        # with only their own address in To, same as the SMTP path.
        "messageVersions": [{"to": [{"email": e}]} for e in to_emails],
    }
    if text_content:
        payload["textContent"] = text_content
    if reply_to:
        payload["replyTo"] = {"email": reply_to}
    if scheduled_at is not None:
        if scheduled_at.tzinfo is None:
            raise ValueError("scheduled_at must be timezone-aware")
        payload["scheduledAt"] = scheduled_at.astimezone(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z"
        )
        if batch_id:
            payload["batchId"] = batch_id

    try:
        resp = requests.post(
            BREVO_API_URL,
            json=payload,
            headers={"api-key": api_key, "accept": "application/json"},
            timeout=30,
        )
    except requests.RequestException as e:
        print(f"::error::Brevo API request failed: {e}")
        return False

    if resp.status_code not in (200, 201, 202):
        # Brevo error bodies are {"code", "message"} and never echo the key.
        print(f"::error::Brevo API rejected the send: HTTP {resp.status_code} {resp.text[:300]}")
        return False

    if scheduled_at is not None:
        local = scheduled_at.astimezone(PACIFIC_TZ)
        print(f"  ✓ Scheduled {len(to_emails)} email(s) for "
              f"{local.strftime('%a %b %-d, %H:%M %Z')} (batch {batch_id})")
    else:
        print(f"  ✓ Sent {len(to_emails)} email(s) via Brevo API")
    return True

BREVO_STATUS_URL = "https://api.brevo.com/v3/smtp/emailStatus/{}"


def resolve_recipients() -> list[str]:
    """Who this run sends to. DIGEST_TEST_EMAIL (a manual test-run override)
    takes precedence over the normal DIGEST_TO_EMAIL list when it is set."""
    raw = os.environ.get("DIGEST_TEST_EMAIL", "").strip() or os.environ.get("DIGEST_TO_EMAIL", "")
    return [e.strip() for e in raw.split(",") if e.strip()]


def brevo_batch_status(batch_id: str) -> str:
    """Whether Brevo holds a scheduled batch: "exists", "absent" or "unknown".

    Only meaningful for 2+ recipients: Brevo keeps a batch record for a
    multi-version send (verified live: visible within 3 s, 404 once
    deleted) but none for a single message, which can only be looked up by
    its messageId.

    Brevo, not state.json, is the authority on whether a morning's digest is
    already queued; state.json can lag if a run scheduled the email but its
    state commit never landed. "unknown" means the lookup itself failed.
    """
    api_key = os.environ.get("BREVO_API_KEY", "").strip()
    if not api_key:
        return "unknown"
    try:
        resp = requests.get(
            BREVO_STATUS_URL.format(batch_id),
            headers={"api-key": api_key, "accept": "application/json"},
            timeout=30,
        )
    except requests.RequestException as e:
        print(f"::warning::Brevo batch lookup failed: {e}")
        return "unknown"
    if resp.status_code == 404:
        return "absent"
    if resp.status_code != 200:
        print(f"::warning::Brevo batch lookup returned HTTP {resp.status_code}")
        return "unknown"
    try:
        body = resp.json()
    except ValueError:
        return "unknown"
    if not isinstance(body, dict):
        return "unknown"
    batches = body.get("batches")
    if batches:
        return "exists"
    if batches == [] or body.get("count") == 0:
        return "absent"
    return "unknown"


def send_digest_email(
    releases: list[dict],
    improvements: list[dict],
    retirements: list[dict],
    to_emails: Optional[list[str]] = None,
    deliver_at: Optional[datetime] = None,
    scheduled_at: Optional[datetime] = None,
    batch_id: Optional[str] = None,
) -> bool:
    """Build and send the changelog digest email.

    deliver_at: the morning this digest is for; it sets the date in the
    subject and header, so a run the evening before is dated for the morning.
    scheduled_at: when Brevo should release it, or None to send now. The
    caller decides this (once, against a fresh clock) so it can't disagree
    with the batch_id it records.
    """
    if not to_emails:
        to_emails = resolve_recipients()

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
    reference = (deliver_at or datetime.now(tz=PACIFIC_TZ)).astimezone(PACIFIC_TZ)
    digest_date = reference.strftime("%A, %B %-d, %Y")
    html_content = build_email_html(releases, improvements, retirements, digest_date)
    text_content = build_email_text(releases, improvements, retirements, digest_date)

    # Build subject line
    total_items = len(releases) + len(improvements) + len(retirements)
    date_str = reference.strftime("%a, %b %-d")
    update_word = "update" if total_items == 1 else "updates"
    if total_items == 0:
        subject = f"No changelog updates today · {date_str}"
    else:
        subject = f"{total_items} new {update_word} in the GitHub Changelog · {date_str}"

    if os.environ.get("BREVO_API_KEY", "").strip():
        return send_email_brevo_api(
            to_emails, subject, html_content, text_content=text_content,
            scheduled_at=scheduled_at, batch_id=batch_id,
        )
    if scheduled_at is not None:
        # Refuse rather than silently sending at the wrong time.
        print("::error::Scheduled delivery requires BREVO_API_KEY (SMTP cannot "
              "schedule). Not sending.")
        return False
    return send_email(to_emails, subject, html_content, text_content=text_content)
