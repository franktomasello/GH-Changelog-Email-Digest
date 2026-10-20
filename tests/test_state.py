"""Tests for the dedup/state layer — the core of the 'no repeats' promise."""

import json
from datetime import datetime, timedelta

import pytest

import state


@pytest.fixture
def state_file(tmp_path, monkeypatch):
    """Point the state module at a throwaway JSON file for each test."""
    path = tmp_path / "state.json"
    monkeypatch.setattr(state, "STATE_FILE", str(path))
    return path


def _write_state(path, urls_with_timestamps):
    path.write_text(json.dumps({"processed_urls": urls_with_timestamps}))


# --- filter / mark (pure dedup logic) ---------------------------------------

def test_filter_new_entries_excludes_seen():
    entries = [{"url": "a"}, {"url": "b"}, {"url": "c"}]
    new = state.filter_new_entries(entries, {"b"})
    assert [e["url"] for e in new] == ["a", "c"]


def test_filter_new_entries_all_seen_returns_empty():
    entries = [{"url": "a"}, {"url": "b"}]
    assert state.filter_new_entries(entries, {"a", "b"}) == []


def test_mark_entries_unions_urls():
    result = state.mark_entries_as_processed([{"url": "b"}, {"url": "c"}], {"a"})
    assert result == {"a", "b", "c"}


# --- save / load round-trip --------------------------------------------------

def test_save_then_load_roundtrip(state_file):
    state.save_processed_urls({"https://x/1", "https://x/2"})
    assert state.load_processed_urls() == {"https://x/1", "https://x/2"}


def test_load_missing_file_returns_empty(state_file):
    assert state.load_processed_urls() == set()


def test_save_preserves_existing_timestamps(state_file):
    # Within the prune window so it survives, but distinct from "now" so we can
    # assert it was preserved rather than rewritten.
    existing_ts = (datetime.now() - timedelta(days=10)).isoformat()
    _write_state(state_file, {"https://x/1": existing_ts})
    # Re-save including the existing URL plus a new one.
    state.save_processed_urls({"https://x/1", "https://x/2"})
    saved = json.loads(state_file.read_text())["processed_urls"]
    assert saved["https://x/1"] == existing_ts  # untouched
    assert "https://x/2" in saved               # newly added


# --- migration from the old list format -------------------------------------

def test_migrate_old_list_format(state_file):
    state_file.write_text(json.dumps({"processed_urls": ["https://x/1", "https://x/2"]}))
    assert state.load_processed_urls() == {"https://x/1", "https://x/2"}


# --- 90-day pruning ----------------------------------------------------------

def test_prune_drops_entries_older_than_max_age(state_file):
    now = datetime.now()
    recent = (now - timedelta(days=1)).isoformat()
    stale = (now - timedelta(days=state.MAX_AGE_DAYS + 5)).isoformat()
    _write_state(state_file, {"https://recent": recent, "https://stale": stale})
    assert state.load_processed_urls() == {"https://recent"}


def test_prune_keeps_entries_within_max_age(state_file):
    now = datetime.now()
    just_inside = (now - timedelta(days=state.MAX_AGE_DAYS - 1)).isoformat()
    _write_state(state_file, {"https://keep": just_inside})
    assert state.load_processed_urls() == {"https://keep"}


def test_save_prunes_stale_timestamp(state_file):
    """A URL whose existing timestamp is stale is pruned even if re-submitted."""
    stale = (datetime.now() - timedelta(days=state.MAX_AGE_DAYS + 5)).isoformat()
    _write_state(state_file, {"https://stale": stale})
    # Re-submit the stale URL alongside a fresh one; its old timestamp survives
    # the merge and should then be pruned out.
    state.save_processed_urls({"https://stale", "https://fresh"})
    saved = json.loads(state_file.read_text())["processed_urls"]
    assert "https://stale" not in saved
    assert "https://fresh" in saved


# --- delivery-slot bookkeeping ------------------------------------------------

from datetime import timezone  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

PT = ZoneInfo("America/Los_Angeles")


def test_delivery_slot_roundtrip(state_file):
    slot = datetime(2026, 10, 21, 6, 0, tzinfo=PT)
    state.save_last_delivery_slot(slot, "batch-1")
    assert state.load_last_delivery_slot() == slot
    assert json.loads(state_file.read_text())["last_delivery"]["batch_id"] == "batch-1"


def test_no_delivery_slot_recorded_yet(state_file):
    _write_state(state_file, {})
    assert state.load_last_delivery_slot() is None


def test_saving_urls_preserves_delivery_slot(state_file):
    # Regression guard: save_processed_urls used to rewrite the whole file as
    # {"processed_urls": ...}, which would erase the slot and let the backup
    # cron schedule a duplicate.
    slot = datetime(2026, 10, 21, 6, 0, tzinfo=PT)
    state.save_last_delivery_slot(slot, "batch-1")
    state.save_processed_urls({"https://github.blog/changelog/a"})
    assert state.load_last_delivery_slot() == slot
    assert state.load_processed_urls() == {"https://github.blog/changelog/a"}


def test_saving_slot_preserves_urls(state_file):
    state.save_processed_urls({"https://github.blog/changelog/a"})
    state.save_last_delivery_slot(datetime(2026, 10, 21, 13, 0, tzinfo=timezone.utc))
    assert state.load_processed_urls() == {"https://github.blog/changelog/a"}


def test_naive_slot_rejected_on_save(state_file):
    with pytest.raises(ValueError):
        state.save_last_delivery_slot(datetime(2026, 10, 21, 6, 0))


@pytest.mark.parametrize("bad", ["not-a-date", "2026-10-21T06:00:00", None, ""])
def test_unusable_stored_slot_reads_as_none(state_file, bad):
    # A naive or garbled value can't be safely compared to an aware "now".
    state_file.write_text(json.dumps({"processed_urls": {}, "last_delivery": {"slot": bad}}))
    assert state.load_last_delivery_slot() is None
