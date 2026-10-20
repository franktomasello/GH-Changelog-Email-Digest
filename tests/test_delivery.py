"""Tests for when the digest lands: next_delivery_slot.

Every case pins "now" explicitly, so DST transitions and late or duplicate
cron runs are exercised without touching the real clock.
"""

from datetime import datetime, timezone

import pytest

import pathlib
import re
import uuid
from datetime import timedelta

from delivery import next_delivery_slot, slot_on, slot_batch_id, DELIVERY_TZ, LOOKAHEAD


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


def local(*args):
    return datetime(*args, tzinfo=DELIVERY_TZ)


# --- the slot itself ----------------------------------------------------------

def test_slot_is_6am_pdt_in_summer():
    slot = slot_on(local(2026, 10, 21).date())
    assert slot.astimezone(timezone.utc) == utc(2026, 10, 21, 13, 0)


def test_slot_is_6am_pst_in_winter():
    slot = slot_on(local(2026, 11, 2).date())
    assert slot.astimezone(timezone.utc) == utc(2026, 11, 2, 14, 0)


def test_slot_on_fall_back_day_is_pst():
    # 2026-11-01: clocks fall back at 2 AM, so 6 AM that day is already PST.
    slot = slot_on(local(2026, 11, 1).date())
    assert slot.astimezone(timezone.utc) == utc(2026, 11, 1, 14, 0)


def test_slot_on_spring_forward_day_is_pdt():
    # 2026-03-08: clocks spring forward at 2 AM, so 6 AM that day is PDT.
    slot = slot_on(local(2026, 3, 8).date())
    assert slot.astimezone(timezone.utc) == utc(2026, 3, 8, 13, 0)


# --- the primary cron run (the evening before) --------------------------------

def test_primary_run_evening_schedules_next_morning_pdt():
    # 03:17 UTC Oct 21 = 20:17 PDT Oct 20 -> deliver 06:00 PDT Oct 21.
    slot = next_delivery_slot(utc(2026, 10, 21, 3, 17))
    assert slot == local(2026, 10, 21, 6, 0)


def test_primary_run_evening_schedules_next_morning_pst():
    # 03:17 UTC Nov 10 = 19:17 PST Nov 9 -> deliver 06:00 PST Nov 10.
    slot = next_delivery_slot(utc(2026, 11, 10, 3, 17))
    assert slot == local(2026, 11, 10, 6, 0)
    assert slot.astimezone(timezone.utc) == utc(2026, 11, 10, 14, 0)




def test_primary_run_across_fall_back_night():
    # Evening of Oct 31 (PDT); the morning of Nov 1 is already PST.
    slot = next_delivery_slot(utc(2026, 11, 1, 3, 17))
    assert slot.astimezone(timezone.utc) == utc(2026, 11, 1, 14, 0)


def test_primary_run_across_spring_forward_night():
    # Evening of Mar 7 (PST); the morning of Mar 8 is already PDT.
    slot = next_delivery_slot(utc(2026, 3, 8, 3, 17))
    assert slot.astimezone(timezone.utc) == utc(2026, 3, 8, 13, 0)


# --- the backup cron run ------------------------------------------------------

def test_backup_run_is_noop_once_primary_claimed_the_slot():
    claimed = local(2026, 10, 21, 6, 0)
    # 06:17 UTC Oct 21 = 23:17 PDT Oct 20.
    assert next_delivery_slot(utc(2026, 10, 21, 6, 17), last_slot=claimed) is None


def test_backup_run_after_midnight_does_not_claim_tomorrow():
    # Even after local midnight, a filled slot must not push the backup run
    # forward onto the following morning (that's what LOOKAHEAD prevents).
    claimed = local(2026, 10, 21, 6, 0)
    now = local(2026, 10, 21, 1, 30)
    assert next_delivery_slot(now, last_slot=claimed) is None


def test_backup_run_delivers_if_primary_never_ran():
    # Primary dropped by GitHub: the backup still fills the morning slot.
    slot = next_delivery_slot(utc(2026, 10, 21, 12, 37), last_slot=local(2026, 10, 20, 6, 0))
    assert slot == local(2026, 10, 21, 6, 0)


# --- late runs ----------------------------------------------------------------

def test_slightly_late_run_returns_past_slot_to_send_now():
    now = local(2026, 10, 21, 6, 40)
    slot = next_delivery_slot(now, last_slot=local(2026, 10, 20, 6, 0))
    assert slot == local(2026, 10, 21, 6, 0)
    assert slot < now  # caller sends immediately


def test_run_too_late_for_todays_slot_does_not_send_it():
    # 13:00 PDT: today's 6 AM is 7h stale, beyond LATE_GRACE, and tomorrow's
    # is 17h out, inside LOOKAHEAD, so this run takes tomorrow's slot.
    now = local(2026, 10, 21, 13, 0)
    assert next_delivery_slot(now, last_slot=local(2026, 10, 20, 6, 0)) == local(2026, 10, 22, 6, 0)


def test_midday_run_with_nothing_in_range_returns_none():
    # 11:00 PDT: today's slot was already filled and tomorrow's is 19h away.
    now = local(2026, 10, 21, 11, 0)
    assert next_delivery_slot(now, last_slot=local(2026, 10, 21, 6, 0)) is None


# --- state edge cases ---------------------------------------------------------

def test_first_ever_run_with_no_history():
    assert next_delivery_slot(utc(2026, 10, 21, 3, 17), last_slot=None) == local(2026, 10, 21, 6, 0)


def test_last_slot_in_a_different_zone_compares_correctly():
    # Stored slots are ISO strings; a UTC-expressed slot must still match.
    claimed_utc = utc(2026, 10, 21, 13, 0)
    assert next_delivery_slot(utc(2026, 10, 21, 6, 17), last_slot=claimed_utc) is None


def test_naive_now_is_rejected():
    with pytest.raises(ValueError):
        next_delivery_slot(datetime(2026, 10, 21, 3, 17))


# --- the crons in digest.yml actually fit the delivery window ------------------

WORKFLOW = pathlib.Path(__file__).resolve().parent.parent / ".github" / "workflows" / "digest.yml"
# Worst queue delay seen on this repo's scheduled runs (late Aug 2026: 9h46m;
# Oct 12: 8h40m). If a longer one shows up, raise this and re-check the crons.
MAX_OBSERVED_DELAY = timedelta(hours=9, minutes=46)


def _cron_times():
    crons = re.findall(r"cron:\s*'(\d+) (\d+) \* \* \*'", WORKFLOW.read_text())
    assert crons, "no daily crons found in digest.yml"
    return [(int(h), int(m)) for m, h in crons]


@pytest.mark.parametrize("evening_utc_day,morning", [
    ((2026, 7, 16), (2026, 7, 16)),    # PDT: slot 13:00 UTC
    ((2026, 12, 16), (2026, 12, 16)),  # PST: slot 14:00 UTC
    ((2026, 3, 8), (2026, 3, 8)),      # spring-forward morning
    ((2026, 11, 1), (2026, 11, 1)),    # fall-back morning
])
def test_every_cron_lands_on_the_right_morning_at_any_delay(evening_utc_day, morning):
    expected = slot_on(local(*morning).date())
    for hour, minute in _cron_times():
        fired = utc(*evening_utc_day, hour, minute)
        delay = timedelta(0)
        while delay <= MAX_OBSERVED_DELAY:
            run = fired + delay
            slot = next_delivery_slot(run)
            assert slot == expected, (hour, minute, delay)
            assert slot > run, f"cron {hour:02}:{minute:02} + {delay} starts after the slot"
            delay += timedelta(minutes=5)


def test_crons_leave_lookahead_headroom():
    # Even a run that fires instantly must still see the morning within LOOKAHEAD.
    for hour, minute in _cron_times():
        run = utc(2026, 12, 16, hour, minute)  # PST: the longest wait to 06:00
        assert slot_on(local(2026, 12, 16).date()) - run <= LOOKAHEAD


# --- slot_batch_id: one stable Brevo batch per morning -------------------------

def test_batch_id_is_stable_for_a_morning():
    a = slot_batch_id(local(2026, 10, 21, 6, 0))
    b = slot_batch_id(utc(2026, 10, 21, 13, 0))  # same instant, other zone
    assert a == b


def test_batch_id_differs_between_mornings():
    assert slot_batch_id(local(2026, 10, 21, 6, 0)) != slot_batch_id(local(2026, 10, 22, 6, 0))


def test_batch_id_is_a_valid_uuid4():
    # Brevo rejects anything that isn't a UUIDv4.
    parsed = uuid.UUID(slot_batch_id(local(2026, 10, 21, 6, 0)))
    assert parsed.version == 4
    assert parsed.variant == uuid.RFC_4122
