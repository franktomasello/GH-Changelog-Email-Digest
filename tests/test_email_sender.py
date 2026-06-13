"""Tests for email building, masking, and SMTP send semantics."""

from unittest import mock

import pytest

import email_sender as es


# --- address masking ---------------------------------------------------------

@pytest.mark.parametrize("addr,masked", [
    ("alice@github.com", "a***@github.com"),
    ("bob@example.org", "b***@example.org"),
    ("noatsign", "n***"),
])
def test_mask_email(addr, masked):
    assert es._mask_email(addr) == masked


# --- plain-text body ---------------------------------------------------------

def test_build_email_text_pluralizes_and_lists_entries():
    releases = [{
        "title": "GHES 3.21 GA", "url": "https://github.blog/x",
        "published": "Jun 12, 2026", "summary": "A big release.",
        "docs_url": "https://docs.github.com/y", "key_features": ["Feat A", "Feat B"],
    }]
    text = es.build_email_text(releases, [], [], digest_date="Friday, June 12, 2026")
    assert "1 new update in the GitHub Changelog." in text   # singular
    assert "== RELEASES (1) ==" in text
    assert "GHES 3.21 GA" in text
    assert "Docs: https://docs.github.com/y" in text
    assert "Link: https://github.blog/x" in text
    assert "- Feat A" in text


def test_build_email_text_plural_and_empty_wording():
    two = es.build_email_text([{"title": "a"}], [{"title": "b"}], [], digest_date="d")
    assert "2 new updates" in two
    none = es.build_email_text([], [], [], digest_date="d")
    assert "No new updates today." in none


# --- SMTP send semantics -----------------------------------------------------

class _FakeSMTP:
    """Minimal SMTP stand-in; raises for any address containing 'bad'."""
    captured_kwargs = {}

    def __init__(self, *args, **kwargs):
        _FakeSMTP.captured_kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, **k):
        pass

    def login(self, *a):
        pass

    def sendmail(self, frm, to, raw):
        if "bad" in to:
            raise RuntimeError("550 mailbox unavailable")


@pytest.fixture
def smtp_env(monkeypatch):
    monkeypatch.setenv("SMTP_USER", "digest@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    monkeypatch.setattr(es.smtplib, "SMTP", _FakeSMTP)


def test_send_email_all_success_returns_true(smtp_env):
    assert es.send_email(["a@x.com", "b@x.com"], "s", "<p>h</p>", "f@x.com") is True


def test_send_email_partial_failure_returns_false(smtp_env):
    # One recipient fails -> must NOT report success (else state advances and the
    # failed recipient permanently loses that day's digest).
    assert es.send_email(["a@x.com", "bad@x.com"], "s", "<p>h</p>", "f@x.com") is False


def test_send_email_sets_socket_timeout(smtp_env):
    es.send_email(["a@x.com"], "s", "<p>h</p>", "f@x.com")
    assert _FakeSMTP.captured_kwargs.get("timeout") == 30


def test_send_email_attaches_text_and_headers(smtp_env):
    captured = {}
    orig = _FakeSMTP.sendmail

    def capture(self, frm, to, raw):
        captured["raw"] = raw
        return orig(self, frm, to, raw)

    with mock.patch.object(_FakeSMTP, "sendmail", capture):
        es.send_email(["a@x.com"], "Subj", "<p>h</p>", "digest@example.com",
                      text_content="plain body")

    import email
    msg = email.message_from_string(captured["raw"])
    assert msg.get_content_type() == "multipart/alternative"
    parts = [p.get_content_type() for p in msg.get_payload()]
    assert parts == ["text/plain", "text/html"]   # least-rich-first ordering
    assert msg["Date"]
    assert msg["Message-ID"]
    assert msg["List-Unsubscribe"] == "<mailto:digest@example.com?subject=unsubscribe>"
