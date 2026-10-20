#!/usr/bin/env python3
"""
GitHub Changelog Email Digest - Main Entry Point

Fetches the GitHub changelog RSS feed, filters to only new entries,
categorizes them, generates demo outlines for releases, and sends
a beautifully formatted email digest.
"""

import argparse
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

from changelog import (
    fetch_changelog,
    categorize_entries,
    enrich_entries,
    entries_to_dict,
)
from state import (
    load_processed_urls,
    save_processed_urls,
    filter_new_entries,
    mark_entries_as_processed,
    load_last_delivery_slot,
    save_last_delivery_slot,
)
from delivery import next_delivery_slot, slot_batch_id, DELIVERY_TZ, MIN_SCHEDULE_LEAD
import email_sender
from dotenv import load_dotenv
load_dotenv()  # Load .env file; existing env vars take precedence


def main():
    parser = argparse.ArgumentParser(
        description="Send GitHub Changelog email digest"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and process entries but don't send email or update state",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force processing even if there are no new entries (for dry-run/preview only)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Include all entries from the past week, ignoring previously sent state (for manual runs)",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Output HTML to stdout instead of sending email",
    )
    parser.add_argument(
        "--scheduled-delivery",
        action="store_true",
        help="Deliver at the next 06:00 Pacific slot (used by the cron runs). "
             "Exits without doing anything if that slot is already filled.",
    )
    parser.add_argument(
        "--deliver-in-minutes",
        type=int,
        metavar="N",
        help="Testing aid: schedule this send N minutes from now instead of "
             "sending immediately. Does not touch delivery-slot state.",
    )
    args = parser.parse_args()
    if args.scheduled_delivery and args.deliver_in_minutes is not None:
        parser.error("--scheduled-delivery and --deliver-in-minutes are mutually exclusive")
    if args.deliver_in_minutes is not None and args.deliver_in_minutes < 2:
        # Anything shorter is inside MIN_SCHEDULE_LEAD and would just send now,
        # silently skipping the scheduling path the flag exists to test.
        parser.error("--deliver-in-minutes must be at least 2")
    if args.scheduled_delivery and os.environ.get("DIGEST_TEST_EMAIL", "").strip():
        # A scheduled send claims the morning's batchId. Claiming it for a
        # single test address would make the real cron run see the morning as
        # already scheduled and skip all recipients.
        parser.error("--scheduled-delivery sends to the full recipient list; unset "
                     "DIGEST_TEST_EMAIL (use --deliver-in-minutes to test scheduling)")

    print(f"🚀 GitHub Changelog Digest - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("-" * 60)

    # Step 1: Load previously processed URLs
    print("📂 Loading state...")
    processed_urls = load_processed_urls()
    print(f"   Found {len(processed_urls)} previously processed entries")

    # Work out when this digest should land, before doing any fetching: a
    # backup cron run whose slot the primary already filled has nothing to do.
    now = datetime.now(timezone.utc)
    slot = None
    deliver_at = None
    precheck = None  # Brevo's answer before we send: "exists"/"absent"/"unknown"
    if args.scheduled_delivery:
        # Only the Brevo API can hold an email until 06:00; SMTP sends now.
        # Fail before fetching, so a missing key is loud and costs nothing.
        if not os.environ.get("BREVO_API_KEY", "").strip():
            print("::error::Scheduled delivery requires the BREVO_API_KEY secret "
                  "(a Brevo API key, not the SMTP key). Nothing was sent.")
            return 1
        slot = next_delivery_slot(now, load_last_delivery_slot())
        if slot is None:
            print("✅ The next 06:00 Pacific delivery is already scheduled (or "
                  "out of range for this run). Nothing to do.")
            return 0
        # state.json can lag behind reality: a run may have scheduled the
        # email and then failed before its state commit landed. Ask Brevo.
        precheck = status = email_sender.brevo_batch_status(slot_batch_id(slot))
        if status == "exists":
            print("✅ Brevo already has this morning's digest scheduled; recording "
                  "the slot without sending again.")
            if args.dry_run or args.preview:
                print("   (dry run / preview: state left untouched)")
                return 0
            # Entries are deliberately not marked processed: we can't tell which
            # ones that batch contained, and repeating an entry tomorrow beats
            # silently dropping one.
            save_last_delivery_slot(slot, slot_batch_id(slot))
            return 0
        if status == "unknown":
            print("::warning::Couldn't confirm with Brevo whether this morning "
                  "is already scheduled; continuing.")
        deliver_at = slot
    elif args.deliver_in_minutes is not None:
        # Provisional, for the log line; re-measured from send time below so
        # slow fetching can't eat the lead time.
        deliver_at = now + timedelta(minutes=args.deliver_in_minutes)
    if deliver_at is not None:
        when = deliver_at.astimezone(DELIVERY_TZ).strftime("%a %b %-d, %H:%M %Z")
        late = " (slot already passed: sending now)" if deliver_at <= now else ""
        print(f"⏰ Delivery target: {when}{late}")

    # Step 2: Fetch changelog entries (only from the past 7 days)
    print("📡 Fetching GitHub changelog (past 7 days)...")
    all_entries = fetch_changelog()
    print(f"   Fetched {len(all_entries)} entries from the past week")

    # Step 3: Filter to only new entries (unless --all flag is set)
    if args.all:
        print("📋 Manual run with --all flag: including all entries from past week")
        new_entries = all_entries
    else:
        print("🔍 Filtering new entries...")
        # Convert to dict format for filtering
        all_entries_dict = [{"url": e.url, "entry": e} for e in all_entries]
        new_entries_dict = filter_new_entries(all_entries_dict, processed_urls)
        new_entries = [e["entry"] for e in new_entries_dict]
    print(f"   Found {len(new_entries)} entries to process")

    # Step 4: Check if we have anything to send
    if not new_entries:
        if args.force and (args.dry_run or args.preview):
            print("⚠️  No new entries, but --force flag set with dry-run/preview. Using recent entries for testing...")
            new_entries = all_entries[:5]  # Limit to 5 for testing
        else:
            print("✅ No new entries to send. Skipping email.")
            return 0

    # Step 5: Categorize entries
    print("📊 Categorizing entries...")
    categorized = categorize_entries(new_entries)
    print(f"   - Releases: {len(categorized['releases'])}")
    print(f"   - Improvements: {len(categorized['improvements'])}")
    print(f"   - Retirements: {len(categorized['retirements'])}")

    # Step 6: Enrich all entries with summary, key features, and a docs link
    print("📝 Enriching entries with summaries and documentation links...")
    enriched_releases = enrich_entries(categorized["releases"])
    enriched_improvements = enrich_entries(categorized["improvements"])
    enriched_retirements = enrich_entries(categorized["retirements"])
    categorized["releases"] = enriched_releases
    categorized["improvements"] = enriched_improvements
    categorized["retirements"] = enriched_retirements

    # Docs-link coverage: surfaced in CI logs so accuracy regressions are visible
    all_enriched = enriched_releases + enriched_improvements + enriched_retirements
    linked_count = sum(1 for e in all_enriched if e.docs_url)
    print(f"   Docs links resolved: {linked_count}/{len(all_enriched)} entries")
    for e in all_enriched:
        if not e.docs_url:
            print(f"     ⚠️  no docs link: {e.title[:70]}")

    # Step 7: Convert to dict format for templating
    releases = entries_to_dict(categorized["releases"])
    improvements = entries_to_dict(categorized["improvements"])
    retirements = entries_to_dict(categorized["retirements"])

    # Step 8: Preview or send
    if args.preview:
        print("👁️  Preview mode - outputting HTML...")
        html = email_sender.build_email_html(releases, improvements, retirements)
        print(html)
        return 0

    if args.dry_run:
        print("🏃 Dry run mode - skipping email send and state update")
        print("✅ Would have sent digest with:")
        print(f"   - {len(releases)} releases")
        print(f"   - {len(improvements)} improvements")
        print(f"   - {len(retirements)} retirements")
        if deliver_at is not None:
            print(f"   - delivery at {deliver_at.astimezone(DELIVERY_TZ).strftime('%a %b %-d, %H:%M %Z')}")
        return 0

    # Step 9: Send email
    print("📧 Sending digest email...")
    # Decide schedule-vs-now exactly once, against a fresh clock: fetching and
    # enrichment take time, and the target may have passed since the run began.
    send_now = datetime.now(timezone.utc)
    if args.deliver_in_minutes is not None:
        deliver_at = send_now + timedelta(minutes=args.deliver_in_minutes)
    scheduled_at = None
    if deliver_at is not None and deliver_at > send_now + MIN_SCHEDULE_LEAD:
        scheduled_at = deliver_at
    elif deliver_at is not None:
        print("   Delivery time has passed (or is under a minute away): sending now.")
    batch_id = None
    if scheduled_at is not None:
        batch_id = slot_batch_id(slot) if slot is not None else str(uuid.uuid4())

    success = email_sender.send_digest_email(
        releases, improvements, retirements,
        deliver_at=deliver_at, scheduled_at=scheduled_at, batch_id=batch_id,
    )
    if scheduled_at is not None and len(email_sender.resolve_recipients()) < 2:
        print("   One recipient: Brevo keeps no batch record for a single message, "
              "so there is no batch to confirm.")
    elif scheduled_at is not None:
        # Brevo is the authority. A timeout can hide a request that actually
        # went through; a 2xx is worth confirming.
        status = email_sender.brevo_batch_status(batch_id)
        if not success and status == "exists" and slot is not None and precheck != "absent":
            # Every run for a morning shares one batchId, so without a clean
            # "absent" beforehand this may be an EARLIER run's batch, which
            # lacks anything published since. Record the morning, but leave
            # the entries unprocessed: repeating beats dropping.
            print("::warning::The send failed, but Brevo already has a batch for this "
                  "morning (possibly from an earlier run). Recording the morning; "
                  "these entries will repeat in the next digest.")
            save_last_delivery_slot(slot, batch_id)
            return 0
        if not success and status == "exists":
            print("::warning::The send reported an error, but Brevo has the batch "
                  "scheduled; treating it as scheduled.")
            success = True
        elif success and status == "exists":
            print("   Brevo confirms the batch is scheduled.")
        elif success:
            print(f"::warning::Brevo accepted the request, but looking the batch up "
                  f"returned '{status}'.")

    if not success:
        print("❌ Failed to send email")
        return 1

    # Step 10: Update state
    print("💾 Updating state...")
    new_urls = mark_entries_as_processed(
        [{"url": e.url} for e in new_entries],
        processed_urls
    )
    save_processed_urls(new_urls)
    print(f"   State now contains {len(new_urls)} processed entries")
    if slot is not None:
        save_last_delivery_slot(slot, batch_id)
        print(f"   Recorded delivery slot {slot.isoformat()}")

    print("-" * 60)
    print("✅ Digest scheduled successfully!" if scheduled_at else "✅ Digest sent successfully!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
