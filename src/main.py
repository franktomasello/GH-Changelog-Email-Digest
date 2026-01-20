#!/usr/bin/env python3
"""
GitHub Changelog Email Digest - Main Entry Point

Fetches the GitHub changelog RSS feed, filters to only new entries,
categorizes them, generates demo outlines for releases, and sends
a beautifully formatted email digest.
"""

import argparse
import sys
from datetime import datetime

from changelog import (
    fetch_changelog,
    categorize_entries,
    enrich_entries_with_demo_outlines,
    entries_to_dict,
)
from state import (
    load_processed_urls,
    save_processed_urls,
    filter_new_entries,
    mark_entries_as_processed,
)
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
        help="Send email even if there are no new entries (useful for testing)",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Output HTML to stdout instead of sending email",
    )
    args = parser.parse_args()

    print(f"🚀 GitHub Changelog Digest - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("-" * 60)

    # Step 1: Load previously processed URLs
    print("📂 Loading state...")
    processed_urls = load_processed_urls()
    print(f"   Found {len(processed_urls)} previously processed entries")

    # Step 2: Fetch changelog entries
    print("📡 Fetching GitHub changelog...")
    all_entries = fetch_changelog()
    print(f"   Fetched {len(all_entries)} total entries from RSS feed")

    # Step 3: Filter to only new entries
    print("🔍 Filtering new entries...")
    # Convert to dict format for filtering
    all_entries_dict = [{"url": e.url, "entry": e} for e in all_entries]
    new_entries_dict = filter_new_entries(all_entries_dict, processed_urls)
    new_entries = [e["entry"] for e in new_entries_dict]
    print(f"   Found {len(new_entries)} new entries")

    # Step 4: Check if we have anything to send
    if not new_entries and not args.force:
        print("✅ No new entries to send. Exiting.")
        return 0

    if not new_entries and args.force:
        print("⚠️  No new entries, but --force flag set. Using all entries for preview...")
        new_entries = all_entries[:5]  # Limit to 5 for testing

    # Step 5: Categorize entries
    print("📊 Categorizing entries...")
    categorized = categorize_entries(new_entries)
    print(f"   - Releases: {len(categorized['releases'])}")
    print(f"   - Improvements: {len(categorized['improvements'])}")
    print(f"   - Retirements: {len(categorized['retirements'])}")

    # Step 6: Enrich all entries with documentation links and demo outlines
    print("📝 Enriching entries with documentation and demo outlines...")
    enriched_releases = enrich_entries_with_demo_outlines(categorized["releases"])
    enriched_improvements = enrich_entries_with_demo_outlines(categorized["improvements"])
    enriched_retirements = enrich_entries_with_demo_outlines(categorized["retirements"])
    categorized["releases"] = enriched_releases
    categorized["improvements"] = enriched_improvements
    categorized["retirements"] = enriched_retirements

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
        return 0

    # Step 9: Send email
    print("📧 Sending digest email...")
    success = email_sender.send_digest_email(releases, improvements, retirements)

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

    print("-" * 60)
    print("✅ Digest sent successfully!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
