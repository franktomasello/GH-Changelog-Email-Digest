"""When the digest lands in inboxes.

Delivery is pinned to a wall-clock time in Pacific, and it is decoupled from
when the job runs. GitHub queues scheduled workflows with a large, variable
delay (worst seen on this repo: 9h46m; tests/test_delivery.py holds the
figure the crons are checked against), so no cron value can promise a
specific minute. Instead the job runs the evening before, builds the digest,
and hands it to the mail provider with a send time of the next 06:00 Pacific.

All functions are pure: they take "now" as an argument so they can be tested
across DST transitions without patching the clock.
"""

import hashlib
import uuid
from datetime import datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

DELIVERY_TZ = ZoneInfo("America/Los_Angeles")
DELIVERY_TIME = time(6, 0)  # 06:00 local, so PDT or PST as the date dictates

# A run that wakes up after a slot has passed still delivers it immediately,
# as long as it is no more than this late; beyond that the day is treated as
# missed and the entries roll into the next morning's digest instead.
LATE_GRACE = timedelta(hours=6)

# Never schedule further ahead than this. It is what stops a backup run, which
# wakes after the primary has already claimed tonight's slot, from reaching
# forward and claiming *tomorrow's* slot with whatever trickled in since.
LOOKAHEAD = timedelta(hours=18)

# Below this much lead time, send immediately rather than schedule: a send
# time that is already past by the time Brevo receives it is not worth the
# undocumented behaviour.
MIN_SCHEDULE_LEAD = timedelta(seconds=60)


def slot_on(day) -> datetime:
    """The delivery instant on a given local calendar date, DST-correct."""
    return datetime.combine(day, DELIVERY_TIME, tzinfo=DELIVERY_TZ)


def slot_batch_id(slot: datetime) -> str:
    """The Brevo batchId for one delivery morning, identical on every run.

    This is what makes a second send for the same morning detectable across
    runs: before scheduling, a run asks Brevo whether this batch already
    exists. (Brevo's IdempotencyKey can't do that job; it expires after 30
    minutes, while the backup run comes hours later.) Brevo requires a valid
    UUIDv4, so the hash is stamped with the v4 version and variant bits.
    """
    day = slot.astimezone(DELIVERY_TZ).date().isoformat()
    digest = hashlib.sha256(f"gh-changelog-digest/{day}".encode()).digest()
    return str(uuid.UUID(bytes=digest[:16], version=4))


def next_delivery_slot(now: datetime, last_slot: Optional[datetime] = None) -> Optional[datetime]:
    """The slot this run should fill, or None if there is nothing to do.

    `now` must be timezone-aware. `last_slot` is the most recent slot already
    filled (scheduled or sent); slots at or before it are never reused, which
    is what makes the primary and backup crons safe to both fire.

    The returned slot may be slightly in the past (a late run inside
    LATE_GRACE): the caller should then send immediately rather than schedule.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    today = now.astimezone(DELIVERY_TZ).date()
    for day in (today, today + timedelta(days=1)):
        slot = slot_on(day)
        if last_slot is not None and slot <= last_slot:
            continue
        if slot < now - LATE_GRACE:
            continue
        if slot > now + LOOKAHEAD:
            continue
        return slot
    return None
