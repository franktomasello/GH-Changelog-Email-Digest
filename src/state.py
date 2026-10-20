"""
State management for tracking processed changelog entries.
Uses a JSON file to persist URLs with timestamps for TTL-based pruning.
Automatically prunes entries older than MAX_AGE_DAYS to prevent unbounded growth.
"""

import json
import os
from datetime import datetime, timedelta
from typing import Set, Dict, Optional

STATE_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "state.json")
MAX_AGE_DAYS = 90  # Prune entries older than this


def _load_raw_state() -> Dict:
    """Load raw state data, handling both old and new formats."""
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"processed_urls": {}}


def _migrate_if_needed(data: Dict) -> Dict[str, str]:
    """
    Migrate from old list format to new dict format with timestamps.
    Old: {"processed_urls": ["url1", "url2"]}
    New: {"processed_urls": {"url1": "2026-01-26T08:00:00", "url2": "..."}}
    """
    urls = data.get("processed_urls", {})
    
    # Already in new format (dict)
    if isinstance(urls, dict):
        return urls
    
    # Old format (list) - migrate with current timestamp
    if isinstance(urls, list):
        now = datetime.now().isoformat()
        return {url: now for url in urls}
    
    return {}


def _prune_old_entries(urls_with_timestamps: Dict[str, str]) -> Dict[str, str]:
    """Remove entries older than MAX_AGE_DAYS."""
    cutoff = (datetime.now() - timedelta(days=MAX_AGE_DAYS)).isoformat()
    return {
        url: ts for url, ts in urls_with_timestamps.items()
        if ts > cutoff
    }


def load_processed_urls() -> Set[str]:
    """Load the set of previously processed entry URLs from state file."""
    data = _load_raw_state()
    urls_with_timestamps = _migrate_if_needed(data)
    # Prune on load to keep state clean
    pruned = _prune_old_entries(urls_with_timestamps)
    return set(pruned.keys())


def save_processed_urls(urls: Set[str]) -> None:
    """
    Save the set of processed entry URLs to state file.
    Preserves existing timestamps and adds new ones for new URLs.
    """
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    
    # Load existing timestamps
    data = _load_raw_state()
    existing = _migrate_if_needed(data)
    
    # Merge: keep existing timestamps, add new URLs with current timestamp
    now = datetime.now().isoformat()
    merged = {url: existing.get(url, now) for url in urls}
    
    # Prune old entries before saving
    pruned = _prune_old_entries(merged)
    
    # Sort by timestamp (newest first) for readability
    sorted_urls = dict(sorted(pruned.items(), key=lambda x: x[1], reverse=True))
    
    # Rewrite only processed_urls; other top-level keys (e.g. last_delivery)
    # are carried over. Writing a fresh {"processed_urls": ...} dict here would
    # silently erase delivery bookkeeping on every run.
    data["processed_urls"] = sorted_urls
    _write_state(data)


def _write_state(data: Dict) -> None:
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(data, f, indent=2)


def load_last_delivery_slot() -> Optional[datetime]:
    """The most recent delivery slot already filled, or None if unknown."""
    raw = _load_raw_state().get("last_delivery") or {}
    slot = raw.get("slot") if isinstance(raw, dict) else None
    if not slot:
        return None
    try:
        parsed = datetime.fromisoformat(slot)
    except ValueError:
        return None
    # A naive value can't be compared against an aware "now"; treat it as
    # unknown rather than guess its zone.
    return parsed if parsed.tzinfo is not None else None


def save_last_delivery_slot(slot: datetime, batch_id: Optional[str] = None) -> None:
    """Record that `slot` has been filled. batch_id identifies a scheduled
    Brevo batch so it can be inspected or cancelled via the API."""
    if slot.tzinfo is None:
        raise ValueError("slot must be timezone-aware")
    data = _load_raw_state()
    data["last_delivery"] = {"slot": slot.isoformat(), "batch_id": batch_id}
    _write_state(data)


def filter_new_entries(entries: list, processed_urls: Set[str]) -> list:
    """Filter out entries that have already been processed."""
    return [entry for entry in entries if entry["url"] not in processed_urls]


def mark_entries_as_processed(entries: list, processed_urls: Set[str]) -> Set[str]:
    """Add entry URLs to the processed set and return the updated set."""
    new_urls = {entry["url"] for entry in entries}
    return processed_urls | new_urls
