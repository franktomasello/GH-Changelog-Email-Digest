"""End-to-end decisions in main(): which morning a run claims, when it must
NOT claim one, and how it reconciles with Brevo. Network, enrichment and
sending are stubbed; state uses a throwaway file and the clock is pinned (and
advanced during "fetching" where a test needs time to pass)."""

import sys
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

import main
import state
from delivery import slot_batch_id

PT = ZoneInfo("America/Los_Angeles")
EVENING = datetime(2026, 10, 21, 0, 47, tzinfo=timezone.utc)  # primary cron, 17:47 PDT Oct 20
MORNING_SLOT = datetime(2026, 10, 21, 6, 0, tzinfo=PT)


class _Clock(datetime):
    current = EVENING

    @classmethod
    def now(cls, tz=None):
        return cls.current if tz is None else cls.current.astimezone(tz)


@pytest.fixture
def harness(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(main, "datetime", _Clock)
    monkeypatch.setenv("BREVO_API_KEY", "xkeysib-test")
    # Explicit recipients: never inherit a developer's .env (load_dotenv runs
    # when main is imported) and match production's multi-recipient batches.
    monkeypatch.setenv("DIGEST_TO_EMAIL", "a@example.com,b@example.com")
    monkeypatch.delenv("DIGEST_TEST_EMAIL", raising=False)
    _Clock.current = EVENING

    h = SimpleNamespace(
        entries=[SimpleNamespace(url="https://github.blog/changelog/a", title="A",
                                 docs_url="https://docs.github.com/a")],
        fetches=0, sends=[], send_result=True, fetch_takes=timedelta(0),
        # brevo_batch_status answers, consumed in order (pre-check, post-check).
        statuses=[], status_calls=[],
    )

    def fetch():
        h.fetches += 1
        _Clock.current += h.fetch_takes
        return list(h.entries)

    def send(releases, improvements, retirements, **kw):
        h.sends.append(kw)
        return h.send_result

    def batch_status(batch_id):
        h.status_calls.append(batch_id)
        return h.statuses.pop(0) if h.statuses else "absent"

    monkeypatch.setattr(main, "fetch_changelog", fetch)
    monkeypatch.setattr(main, "categorize_entries",
                        lambda es: {"releases": list(es), "improvements": [], "retirements": []})
    monkeypatch.setattr(main, "enrich_entries", lambda es: es)
    monkeypatch.setattr(main, "entries_to_dict", lambda es: [{"title": e.title} for e in es])
    monkeypatch.setattr(main.email_sender, "send_digest_email", send)
    monkeypatch.setattr(main.email_sender, "brevo_batch_status", batch_status)

    def run(*argv):
        monkeypatch.setattr(sys, "argv", ["main.py", *argv])
        return main.main()
    h.run = run
    return h


# --- the normal night ---------------------------------------------------------

def test_scheduled_run_claims_next_morning(harness):
    harness.statuses = ["absent", "exists"]  # not yet queued; confirmed after
    assert harness.run("--scheduled-delivery") == 0
    [kw] = harness.sends
    assert kw["deliver_at"] == MORNING_SLOT
    assert kw["scheduled_at"] == MORNING_SLOT
    assert kw["batch_id"] == slot_batch_id(MORNING_SLOT)
    assert harness.status_calls == [slot_batch_id(MORNING_SLOT)] * 2
    assert state.load_last_delivery_slot() == MORNING_SLOT
    assert state.load_processed_urls() == {"https://github.blog/changelog/a"}


def test_backup_run_does_nothing_once_morning_is_claimed(harness):
    harness.run("--scheduled-delivery")
    harness.entries.append(SimpleNamespace(url="https://github.blog/changelog/b",
                                           title="B", docs_url=""))
    _Clock.current = datetime(2026, 10, 21, 2, 17, tzinfo=timezone.utc)
    fetches_before, statuses_before = harness.fetches, len(harness.status_calls)

    assert harness.run("--scheduled-delivery") == 0
    assert len(harness.sends) == 1           # no second email for the same morning
    assert harness.fetches == fetches_before  # bailed out before fetching
    assert len(harness.status_calls) == statuses_before  # didn't even ask Brevo
    assert "https://github.blog/changelog/b" not in state.load_processed_urls()


def test_no_new_entries_leaves_morning_unclaimed(harness):
    harness.entries.clear()
    assert harness.run("--scheduled-delivery") == 0
    assert harness.sends == []
    assert state.load_last_delivery_slot() is None  # backup run may still fill it


# --- failure handling ---------------------------------------------------------

def test_missing_api_key_fails_fast_before_any_work(harness, monkeypatch, capsys):
    monkeypatch.delenv("BREVO_API_KEY")
    assert harness.run("--scheduled-delivery") == 1
    assert harness.fetches == 0 and harness.sends == [] and harness.status_calls == []
    assert "requires the BREVO_API_KEY" in capsys.readouterr().out


def test_failed_send_claims_nothing(harness):
    harness.send_result = False
    harness.statuses = ["absent", "absent"]
    assert harness.run("--scheduled-delivery") == 1
    assert state.load_last_delivery_slot() is None
    assert state.load_processed_urls() == set()


def test_send_error_but_brevo_has_the_batch_counts_as_scheduled(harness):
    # e.g. a read timeout after Brevo had already queued the batch
    harness.send_result = False
    harness.statuses = ["absent", "exists"]
    assert harness.run("--scheduled-delivery") == 0
    assert state.load_last_delivery_slot() == MORNING_SLOT
    assert state.load_processed_urls() == {"https://github.blog/changelog/a"}


# --- reconciling with Brevo when state.json is stale --------------------------

def test_brevo_already_queued_records_slot_without_resending(harness, capsys):
    # A previous run scheduled this morning but its state commit never landed.
    harness.statuses = ["exists"]
    assert harness.run("--scheduled-delivery") == 0
    assert harness.sends == [] and harness.fetches == 0
    assert state.load_last_delivery_slot() == MORNING_SLOT
    # Entries stay unprocessed on purpose: repeating beats silently dropping.
    assert state.load_processed_urls() == set()
    assert "already has this morning's digest" in capsys.readouterr().out


def test_unconfirmable_precheck_still_delivers(harness, capsys):
    harness.statuses = ["unknown", "exists"]
    assert harness.run("--scheduled-delivery") == 0
    assert len(harness.sends) == 1
    assert "Couldn't confirm" in capsys.readouterr().out


# --- one clock, one decision ---------------------------------------------------

def test_slot_passing_during_the_run_sends_now_without_a_batch(harness, capsys):
    # Starts 30 s before 06:00 PT; fetching takes a minute.
    _Clock.current = datetime(2026, 10, 21, 12, 59, 30, tzinfo=timezone.utc)
    harness.fetch_takes = timedelta(seconds=60)
    harness.statuses = ["absent"]
    assert harness.run("--scheduled-delivery") == 0
    [kw] = harness.sends
    assert kw["deliver_at"] == MORNING_SLOT   # still dated for this morning
    assert kw["scheduled_at"] is None and kw["batch_id"] is None
    assert harness.status_calls == [slot_batch_id(MORNING_SLOT)]  # no post-check
    out = capsys.readouterr().out
    assert "sending now" in out and "Digest sent successfully" in out
    assert "scheduled successfully" not in out


def test_under_a_minute_of_lead_sends_now(harness):
    _Clock.current = datetime(2026, 10, 21, 12, 59, 30, tzinfo=timezone.utc)
    harness.statuses = ["absent"]
    harness.run("--scheduled-delivery")
    assert harness.sends[0]["scheduled_at"] is None


# --- manual runs ----------------------------------------------------------------

def test_deliver_in_minutes_schedules_without_claiming_a_slot(harness):
    harness.statuses = ["exists"]  # the post-schedule confirmation
    assert harness.run("--deliver-in-minutes", "5") == 0
    [kw] = harness.sends
    assert kw["scheduled_at"] - EVENING == timedelta(minutes=5)
    uuid.UUID(kw["batch_id"])          # a fresh UUID, not a morning's id
    assert kw["batch_id"] != slot_batch_id(MORNING_SLOT)
    assert harness.status_calls == [kw["batch_id"]]  # verified, like production
    assert state.load_last_delivery_slot() is None


def test_plain_run_sends_immediately(harness):
    harness.run()
    [kw] = harness.sends
    assert kw["deliver_at"] is None and kw["scheduled_at"] is None and kw["batch_id"] is None
    assert harness.status_calls == []


@pytest.mark.parametrize("argv", [
    ["--scheduled-delivery", "--deliver-in-minutes", "5"],
    ["--deliver-in-minutes", "0"],
])
def test_bad_flag_combinations_rejected(harness, argv):
    with pytest.raises(SystemExit):
        harness.run(*argv)


# --- round-2 review regressions -------------------------------------------------

def test_failed_send_after_unknown_precheck_does_not_mark_entries(harness):
    # The "exists" seen afterwards may be an EARLIER run's batch, which lacks
    # this run's newer entries: record the morning but don't drop anything.
    harness.send_result = False
    harness.statuses = ["unknown", "exists"]
    assert harness.run("--scheduled-delivery") == 0
    assert state.load_last_delivery_slot() == MORNING_SLOT
    assert state.load_processed_urls() == set()


@pytest.mark.parametrize("flag", ["--dry-run", "--preview"])
def test_dry_run_and_preview_never_write_state(harness, flag):
    harness.statuses = ["exists"]
    assert harness.run("--scheduled-delivery", flag) == 0
    assert state.load_last_delivery_slot() is None
    assert harness.sends == []


def test_scheduled_delivery_refuses_a_test_recipient(harness, monkeypatch):
    # It would claim the morning's batchId for one address, and the real cron
    # would then skip all 14 recipients as "already scheduled".
    monkeypatch.setenv("DIGEST_TEST_EMAIL", "me@example.com")
    with pytest.raises(SystemExit):
        harness.run("--scheduled-delivery")
    assert harness.sends == [] and harness.status_calls == []


def test_deliver_in_minutes_one_is_rejected(harness):
    with pytest.raises(SystemExit):
        harness.run("--deliver-in-minutes", "1")


def test_deliver_in_minutes_is_measured_from_send_time(harness):
    # Slow fetching must not eat the lead time and silently send now.
    harness.fetch_takes = timedelta(seconds=90)
    harness.statuses = ["exists"]
    assert harness.run("--deliver-in-minutes", "2") == 0
    [kw] = harness.sends
    assert kw["scheduled_at"] == EVENING + timedelta(seconds=90) + timedelta(minutes=2)



def test_single_recipient_skips_batch_confirmation(harness, monkeypatch, capsys):
    # Verified live: Brevo keeps no batch record for a one-message send, so a
    # lookup would 404 and raise a false alarm.
    monkeypatch.setenv("DIGEST_TO_EMAIL", "a@example.com")
    assert harness.run("--deliver-in-minutes", "5") == 0
    assert harness.status_calls == []
    out = capsys.readouterr().out
    assert "no batch to confirm" in out and "::warning::" not in out
