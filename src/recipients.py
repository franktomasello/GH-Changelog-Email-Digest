"""Recipient list parsing and resolution.

Single source of truth for ``recipients.txt`` semantics. Used by
``email_sender`` (at digest send time) and by ``bin/add-recipient`` (at edit
time) so both tools agree on what counts as a recipient line.
"""
from __future__ import annotations

import os
from typing import Optional

DEFAULT_FILE = os.path.join(os.path.dirname(__file__), "..", "recipients.txt")


def parse_line(raw: str) -> Optional[str]:
    """Pull a single email out of a ``recipients.txt`` line.

    Returns ``None`` for blank lines and full-line comments. Trailing inline
    comments preceded by whitespace + ``#`` are stripped (we require the leading
    whitespace so we don't accidentally chop a ``#`` out of an email's local
    part — RFC 5322 permits it).
    """
    line = raw
    for sep in (" #", "\t#"):
        if sep in line:
            line = line.split(sep, 1)[0]
            break
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    return line


def _dedupe(emails) -> list[str]:
    """Return ``emails`` with case-insensitive duplicates removed, preserving
    first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for e in emails:
        key = e.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


def load_from_file(path: str) -> list[str]:
    """Read a recipients file and return a deduped list in file order.
    Returns ``[]`` for missing or unreadable files."""
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        emails = (parse_line(line) for line in f)
        return _dedupe(e for e in emails if e)


def split_csv(value: str) -> list[str]:
    """Split a comma-separated env-var value into a clean, deduped email list."""
    return _dedupe(e for e in (chunk.strip() for chunk in value.split(",")) if e)


def resolve(
    explicit: Optional[list[str]] = None,
    recipients_file: Optional[str] = None,
) -> tuple[list[str], str]:
    """Decide who the digest goes to.

    Precedence (first non-empty source wins):
      1. ``explicit`` argument         — programmatic override
      2. ``DIGEST_TEST_EMAIL`` env var — workflow ``test_email`` input
      3. recipients file               — the canonical, git-tracked list
      4. ``DIGEST_TO_EMAIL`` env var   — legacy fallback (comma-separated)

    Returns ``(emails, source)`` where ``source`` is a short label suitable
    for logging. The result is always deduped (case-insensitive).
    """
    if explicit:
        return _dedupe(explicit), "explicit argument"

    test = os.environ.get("DIGEST_TEST_EMAIL", "").strip()
    if test:
        return split_csv(test), "DIGEST_TEST_EMAIL env"

    path = recipients_file or os.environ.get("RECIPIENTS_FILE") or DEFAULT_FILE
    file_emails = load_from_file(path)
    if file_emails:
        return file_emails, f"file ({path})"

    legacy = os.environ.get("DIGEST_TO_EMAIL", "").strip()
    if legacy:
        return split_csv(legacy), "DIGEST_TO_EMAIL env (legacy)"

    return [], "none"
