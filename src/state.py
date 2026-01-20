"""
State management for tracking processed changelog entries.
Uses a JSON file to persist the set of already-sent entry URLs.
"""

import json
import os
from typing import Set

STATE_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "state.json")


def load_processed_urls() -> Set[str]:
    """Load the set of previously processed entry URLs from state file."""
    try:
        with open(STATE_FILE, "r") as f:
            data = json.load(f)
            return set(data.get("processed_urls", []))
    except FileNotFoundError:
        return set()
    except json.JSONDecodeError:
        return set()


def save_processed_urls(urls: Set[str]) -> None:
    """Save the set of processed entry URLs to state file."""
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump({"processed_urls": sorted(list(urls))}, f, indent=2)


def filter_new_entries(entries: list, processed_urls: Set[str]) -> list:
    """Filter out entries that have already been processed."""
    return [entry for entry in entries if entry["url"] not in processed_urls]


def mark_entries_as_processed(entries: list, processed_urls: Set[str]) -> Set[str]:
    """Add entry URLs to the processed set and return the updated set."""
    new_urls = {entry["url"] for entry in entries}
    return processed_urls | new_urls
