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


# --- From display name / Reply-To --------------------------------------------

def _send_and_capture(monkeypatch, **env):
    """Send one message and return (envelope_sender, parsed_message)."""
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    captured = {}
    orig = _FakeSMTP.sendmail

    def capture(self, frm, to, raw):
        captured["frm"] = frm
        captured["raw"] = raw
        return orig(self, frm, to, raw)

    with mock.patch.object(_FakeSMTP, "sendmail", capture):
        es.send_email(["a@x.com"], "Subj", "<p>h</p>", "digest@example.com")

    import email
    return captured["frm"], email.message_from_string(captured["raw"])


def test_reply_to_set_when_configured(smtp_env, monkeypatch):
    _, msg = _send_and_capture(monkeypatch, SMTP_REPLY_TO="frank@work.example")
    assert msg["Reply-To"] == "frank@work.example"


def test_no_reply_to_header_when_unset(smtp_env, monkeypatch):
    monkeypatch.delenv("SMTP_REPLY_TO", raising=False)
    _, msg = _send_and_capture(monkeypatch)
    assert msg["Reply-To"] is None


def test_from_name_only_decorates_header_not_envelope(smtp_env, monkeypatch):
    # The display name must never leak into the SMTP envelope sender: relays
    # reject a MAIL FROM that isn't a bare address.
    frm, msg = _send_and_capture(monkeypatch, SMTP_FROM_NAME="Frank Tomasello")
    assert frm == "digest@example.com"
    assert msg["From"] == "Frank Tomasello <digest@example.com>"
    # List-Unsubscribe and Message-ID still derive from the bare address.
    assert msg["List-Unsubscribe"] == "<mailto:digest@example.com?subject=unsubscribe>"
    assert "example.com" in msg["Message-ID"]


def test_from_name_with_comma_is_quoted(smtp_env, monkeypatch):
    # A bare comma would split the From header into two addresses.
    _, msg = _send_and_capture(monkeypatch, SMTP_FROM_NAME="Tomasello, Frank")
    assert msg["From"] == '"Tomasello, Frank" <digest@example.com>'


def test_from_header_is_bare_when_no_name(smtp_env, monkeypatch):
    monkeypatch.delenv("SMTP_FROM_NAME", raising=False)
    _, msg = _send_and_capture(monkeypatch)
    assert msg["From"] == "digest@example.com"


# --- Brevo HTTP API (scheduled delivery) ---------------------------------------

from datetime import datetime, timezone, timedelta  # noqa: E402

import requests  # noqa: E402


class _Resp:
    def __init__(self, status=201, body=None):
        self.status_code = status
        self._body = body if body is not None else {"messageIds": ["<x@smtp-relay.mailin.fr>"]}
        self.text = str(self._body)

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


@pytest.fixture
def brevo_env(monkeypatch):
    monkeypatch.setenv("BREVO_API_KEY", "xkeysib-test")
    monkeypatch.setenv("SMTP_FROM_EMAIL", "digest@example.com")
    monkeypatch.setenv("SMTP_FROM_NAME", "Frank Tomasello")
    monkeypatch.setenv("SMTP_REPLY_TO", "frank@work.example")
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return _Resp()

    monkeypatch.setattr(es.requests, "post", fake_post)
    return calls


def test_brevo_scheduled_payload(brevo_env):
    at = datetime(2026, 10, 21, 13, 0, tzinfo=timezone.utc)
    ok = es.send_email_brevo_api(
        ["a@x.com", "b@x.com"], "Subj", "<p>h</p>", text_content="plain",
        scheduled_at=at, batch_id="11111111-1111-4111-8111-111111111111",
    )
    assert ok is True
    call = brevo_env[0]
    body = call["json"]
    assert call["url"] == "https://api.brevo.com/v3/smtp/email"
    assert call["headers"]["api-key"] == "xkeysib-test"
    assert call["timeout"] == 30
    assert body["sender"] == {"email": "digest@example.com", "name": "Frank Tomasello"}
    assert body["replyTo"] == {"email": "frank@work.example"}
    assert body["subject"] == "Subj"
    assert body["htmlContent"] == "<p>h</p>"
    assert body["textContent"] == "plain"
    # One version per recipient: nobody sees anyone else's address.
    assert body["messageVersions"] == [{"to": [{"email": "a@x.com"}]}, {"to": [{"email": "b@x.com"}]}]
    assert "to" not in body
    assert body["scheduledAt"] == "2026-10-21T13:00:00.000Z"
    assert body["batchId"] == "11111111-1111-4111-8111-111111111111"
    # No custom headers: Brevo's IdempotencyKey expires after 30 minutes and is
    # documented as UUID-only, so it was dropped in favour of a stable batchId.
    assert "headers" not in body


def test_brevo_scheduled_at_is_converted_to_utc(brevo_env):
    from zoneinfo import ZoneInfo
    at = datetime(2026, 11, 2, 6, 0, tzinfo=ZoneInfo("America/Los_Angeles"))  # PST
    es.send_email_brevo_api(["a@x.com"], "s", "<p>h</p>", scheduled_at=at)
    assert brevo_env[0]["json"]["scheduledAt"] == "2026-11-02T14:00:00.000Z"


def test_brevo_immediate_send_has_no_schedule_fields(brevo_env):
    es.send_email_brevo_api(["a@x.com"], "s", "<p>h</p>", batch_id="ignored-without-schedule")
    body = brevo_env[0]["json"]
    assert "scheduledAt" not in body
    assert "batchId" not in body


def test_brevo_optional_fields_omitted_when_unset(brevo_env, monkeypatch):
    monkeypatch.delenv("SMTP_FROM_NAME")
    monkeypatch.delenv("SMTP_REPLY_TO")
    es.send_email_brevo_api(["a@x.com"], "s", "<p>h</p>")
    body = brevo_env[0]["json"]
    assert body["sender"] == {"email": "digest@example.com"}
    assert "replyTo" not in body
    assert "textContent" not in body


def test_brevo_http_error_returns_false(brevo_env, monkeypatch):
    monkeypatch.setattr(es.requests, "post", lambda *a, **k: _Resp(400, {"code": "invalid_parameter"}))
    assert es.send_email_brevo_api(["a@x.com"], "s", "<p>h</p>") is False


def test_brevo_network_error_returns_false(brevo_env, monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("down")
    monkeypatch.setattr(es.requests, "post", boom)
    assert es.send_email_brevo_api(["a@x.com"], "s", "<p>h</p>") is False


def test_brevo_requires_explicit_from_address(brevo_env, monkeypatch):
    # Must not fall back to SMTP_USER, which for Brevo is a relay login.
    monkeypatch.delenv("SMTP_FROM_EMAIL")
    monkeypatch.setenv("SMTP_USER", "b9d585001@smtp-brevo.com")
    with pytest.raises(ValueError):
        es.send_email_brevo_api(["a@x.com"], "s", "<p>h</p>")


def test_brevo_rejects_naive_schedule(brevo_env):
    with pytest.raises(ValueError):
        es.send_email_brevo_api(["a@x.com"], "s", "<p>h</p>", scheduled_at=datetime(2026, 10, 21, 13, 0))


# --- brevo_batch_status: is a morning already queued? -------------------------

def _status_with(monkeypatch, resp=None, exc=None):
    seen = {}

    def fake_get(url, headers=None, timeout=None):
        seen.update(url=url, headers=headers, timeout=timeout)
        if exc:
            raise exc
        return resp

    monkeypatch.setattr(es.requests, "get", fake_get)
    return seen


def test_batch_status_exists(brevo_env, monkeypatch):
    seen = _status_with(monkeypatch, _Resp(200, {"count": 1, "batches": [
        {"scheduledAt": "2026-10-21T13:00:00Z", "createdAt": "2026-10-21T03:00:00Z", "status": "queued"}]}))
    assert es.brevo_batch_status("b-1") == "exists"
    assert seen["url"] == "https://api.brevo.com/v3/smtp/emailStatus/b-1"
    assert seen["headers"]["api-key"] == "xkeysib-test"
    assert seen["timeout"] == 30


@pytest.mark.parametrize("resp", [
    _Resp(404, {"code": "document_not_found"}),
    _Resp(200, {"count": 0, "batches": []}),
])
def test_batch_status_absent(brevo_env, monkeypatch, resp):
    _status_with(monkeypatch, resp)
    assert es.brevo_batch_status("b-1") == "absent"


@pytest.mark.parametrize("resp,exc", [
    (None, requests.ConnectionError("down")),
    (_Resp(500, {"code": "internal"}), None),
    (_Resp(200, ValueError("not json")), None),
    (_Resp(200, ["unexpected"]), None),
    (_Resp(200, {"something": "else"}), None),
])
def test_batch_status_unknown(brevo_env, monkeypatch, resp, exc):
    _status_with(monkeypatch, resp, exc)
    assert es.brevo_batch_status("b-1") == "unknown"


def test_batch_status_without_key_is_unknown(monkeypatch):
    monkeypatch.delenv("BREVO_API_KEY", raising=False)
    called = []
    monkeypatch.setattr(es.requests, "get", lambda *a, **k: called.append(1))
    assert es.brevo_batch_status("b-1") == "unknown"
    assert called == []


# --- send_digest_email: transport choice and digest dating ---------------------

def _digest_args():
    return ([{"title": "T", "url": "https://github.blog/changelog/x", "published": "Oct 20",
              "summary": "", "category": "releases", "labels": [], "docs_url": "",
              "key_features": []}], [], [])


def test_digest_schedules_via_brevo_when_told_to(brevo_env, monkeypatch):
    monkeypatch.setenv("DIGEST_TO_EMAIL", "a@x.com,b@x.com")
    at = datetime.now(timezone.utc) + timedelta(hours=8)
    assert es.send_digest_email(*_digest_args(), deliver_at=at, scheduled_at=at, batch_id="b-1") is True
    body = brevo_env[0]["json"]
    assert body["scheduledAt"] == at.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    assert body["batchId"] == "b-1"


def test_digest_does_not_second_guess_the_callers_decision(brevo_env, monkeypatch):
    # A future deliver_at with scheduled_at=None means "send now": the sender
    # must not re-decide against its own clock (that disagreement is how a
    # batch_id could be recorded for a batch Brevo never received).
    monkeypatch.setenv("DIGEST_TO_EMAIL", "a@x.com")
    at = datetime.now(timezone.utc) + timedelta(hours=8)
    es.send_digest_email(*_digest_args(), deliver_at=at, scheduled_at=None)
    assert "scheduledAt" not in brevo_env[0]["json"]


def test_digest_is_dated_for_delivery_morning(brevo_env, monkeypatch):
    from zoneinfo import ZoneInfo
    monkeypatch.setenv("DIGEST_TO_EMAIL", "a@x.com")
    # Built the evening before; must read as the morning it's delivered.
    at = datetime.now(ZoneInfo("America/Los_Angeles")).replace(hour=6, minute=0, second=0, microsecond=0) + timedelta(days=1)
    es.send_digest_email(*_digest_args(), deliver_at=at, scheduled_at=at)
    body = brevo_env[0]["json"]
    assert at.strftime("%a, %b %-d") in body["subject"]
    assert at.strftime("%A, %B %-d, %Y") in body["htmlContent"]


def test_digest_without_api_key_uses_smtp(smtp_env, monkeypatch):
    monkeypatch.delenv("BREVO_API_KEY", raising=False)
    monkeypatch.setenv("DIGEST_TO_EMAIL", "a@x.com")
    called = []
    monkeypatch.setattr(es.requests, "post", lambda *a, **k: called.append(1))
    assert es.send_digest_email(*_digest_args()) is True
    assert called == []


def test_digest_refuses_to_schedule_without_api_key(smtp_env, monkeypatch):
    # SMTP can't schedule; sending now would land at the wrong time.
    monkeypatch.delenv("BREVO_API_KEY", raising=False)
    monkeypatch.setenv("DIGEST_TO_EMAIL", "a@x.com")
    sent = []
    monkeypatch.setattr(_FakeSMTP, "sendmail", lambda self, *a: sent.append(a))
    at = datetime.now(timezone.utc) + timedelta(hours=8)
    assert es.send_digest_email(*_digest_args(), deliver_at=at, scheduled_at=at) is False
    assert sent == []



def test_brevo_batch_send_202_counts_as_success(brevo_env, monkeypatch):
    # Live response for a multi-version scheduled send is 202, not 201.
    monkeypatch.setattr(es.requests, "post", lambda *a, **k: _Resp(202, {
        "batchId": "34310c0f-a551-4a16-9ca8-166c4cd181e8",
        "messageIds": ["<1@smtp-relay.mailin.fr>", "<2@smtp-relay.mailin.fr>"]}))
    at = datetime.now(timezone.utc) + timedelta(hours=1)
    assert es.send_email_brevo_api(["a@x.com", "b@x.com"], "s", "<p>h</p>", scheduled_at=at) is True


def test_batch_status_parses_live_queued_shape(brevo_env, monkeypatch):
    # Exact body Brevo returned for a queued 2-version batch.
    _status_with(monkeypatch, _Resp(200, {"count": 1, "batches": [{
        "scheduledAt": "2026-10-20T19:01:27.000Z", "createdAt": "2026-10-20T18:51:27.632Z",
        "status": "queued"}]}))
    assert es.brevo_batch_status("34310c0f-a551-4a16-9ca8-166c4cd181e8") == "exists"


def test_batch_status_live_not_found_shape(brevo_env, monkeypatch):
    _status_with(monkeypatch, _Resp(404, {"code": "document_not_found",
                                          "message": "Record not found for batchId/messageId"}))
    assert es.brevo_batch_status("x") == "absent"


@pytest.mark.parametrize("test_email,to_email,expected", [
    ("", "a@x.com, b@x.com", ["a@x.com", "b@x.com"]),
    ("me@x.com", "a@x.com,b@x.com", ["me@x.com"]),
    ("", "", []),
])
def test_resolve_recipients(monkeypatch, test_email, to_email, expected):
    monkeypatch.setenv("DIGEST_TEST_EMAIL", test_email)
    monkeypatch.setenv("DIGEST_TO_EMAIL", to_email)
    assert es.resolve_recipients() == expected
