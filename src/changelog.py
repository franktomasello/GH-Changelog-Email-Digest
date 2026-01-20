"""
Changelog fetching, parsing, categorization, and demo outline extraction.
"""

import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urljoin

import feedparser
import requests
from bs4 import BeautifulSoup

CHANGELOG_FEED_URL = "https://github.blog/changelog/feed/"
DOCS_BASE_URL = "https://docs.github.com"


@dataclass
class ChangelogEntry:
    """Represents a single changelog entry."""
    title: str
    url: str
    published: str
    summary: str
    content_html: str
    category: str  # "Release", "Improvement", or "Retired"
    labels: list = field(default_factory=list)
    demo_outline: Optional[str] = None
    navigation_path: Optional[str] = None
    docs_url: Optional[str] = None


def fetch_changelog() -> list[ChangelogEntry]:
    """Fetch and parse the GitHub changelog RSS feed."""
    feed = feedparser.parse(CHANGELOG_FEED_URL)
    entries = []

    for item in feed.entries:
        # Extract category type (Release, Improvement, Retired)
        category = "Improvement"  # Default
        labels = []

        for tag in item.get("tags", []):
            if tag.get("scheme") == "changelog-type":
                category = tag.get("term", "Improvement")
            elif tag.get("scheme") == "changelog-label":
                labels.append(tag.get("term", ""))

        # Get content (prefer full content over summary)
        content_html = ""
        if hasattr(item, "content") and item.content:
            content_html = item.content[0].get("value", "")
        elif hasattr(item, "summary"):
            content_html = item.summary

        entry = ChangelogEntry(
            title=item.get("title", ""),
            url=item.get("link", ""),
            published=item.get("published", ""),
            summary=item.get("summary", ""),
            content_html=content_html,
            category=category,
            labels=labels,
        )
        entries.append(entry)

    return entries


def categorize_entries(entries: list[ChangelogEntry]) -> dict[str, list[ChangelogEntry]]:
    """Categorize entries into releases, improvements, and retirements."""
    categorized = {
        "releases": [],
        "improvements": [],
        "retirements": [],
    }

    for entry in entries:
        if entry.category == "Release":
            categorized["releases"].append(entry)
        elif entry.category == "Retired":
            categorized["retirements"].append(entry)
        else:
            categorized["improvements"].append(entry)

    return categorized


def extract_docs_url(content_html: str) -> Optional[str]:
    """Extract the docs.github.com URL from changelog content."""
    soup = BeautifulSoup(content_html, "html.parser")

    # Look for docs.github.com links
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if "docs.github.com" in href:
            return href

    return None


def extract_navigation_path(docs_url: str) -> Optional[str]:
    """Scrape a docs page to extract navigation/access instructions."""
    try:
        response = requests.get(docs_url, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        # Look for navigation instructions in common patterns
        # Pattern 1: Look for "how to access" or "navigate to" sections
        text_content = soup.get_text().lower()

        # Pattern 2: Look for breadcrumb-style navigation in the content
        # Common patterns: "Settings > Feature > Sub-feature"
        settings_patterns = [
            r"(?:navigate to|go to|click|select|open)\s+[\"']?([^\"'\n]+(?:>|→|›)[^\"'\n]+)[\"']?",
            r"(?:Settings|Repository|Organization|Profile)\s*(?:>|→|›)\s*[^\n]+",
        ]

        for pattern in settings_patterns:
            matches = re.findall(pattern, soup.get_text(), re.IGNORECASE)
            if matches:
                # Clean up the match
                nav_path = matches[0].strip()
                # Normalize arrows
                nav_path = re.sub(r"\s*[>›]\s*", " → ", nav_path)
                return nav_path

        # Pattern 3: Look for ordered lists that might be steps
        ol_elements = soup.find_all("ol")
        for ol in ol_elements:
            items = ol.find_all("li")
            if 2 <= len(items) <= 6:
                steps = [li.get_text().strip() for li in items[:5]]
                if any("click" in s.lower() or "select" in s.lower() or "navigate" in s.lower() for s in steps):
                    return " → ".join(steps)

        return None

    except Exception:
        return None


def generate_demo_outline(entry: ChangelogEntry) -> str:
    """Generate a demo outline for a release entry."""
    outline_parts = []

    # Title and overview
    outline_parts.append(f"## {entry.title}")
    outline_parts.append("")

    # Extract key points from summary
    if entry.summary:
        soup = BeautifulSoup(entry.summary, "html.parser")
        summary_text = soup.get_text().strip()
        if summary_text:
            outline_parts.append(f"**Overview:** {summary_text[:300]}{'...' if len(summary_text) > 300 else ''}")
            outline_parts.append("")

    # Navigation path
    if entry.navigation_path:
        outline_parts.append("**Navigation Path:**")
        outline_parts.append(f"`{entry.navigation_path}`")
        outline_parts.append("")

    # Demo steps
    outline_parts.append("**Demo Steps:**")

    if entry.navigation_path:
        # Parse navigation path into steps
        steps = entry.navigation_path.split(" → ")
        for i, step in enumerate(steps, 1):
            outline_parts.append(f"{i}. Navigate to **{step.strip()}**")
    else:
        # Generic demo steps based on labels
        outline_parts.append("1. Log into GitHub")
        if "copilot" in " ".join(entry.labels).lower():
            outline_parts.append("2. Navigate to **Settings → Copilot**")
            outline_parts.append("3. Locate the new feature in the settings panel")
        elif "actions" in " ".join(entry.labels).lower():
            outline_parts.append("2. Navigate to a repository's **Actions** tab")
            outline_parts.append("3. Explore the new workflow capabilities")
        elif "security" in " ".join(entry.labels).lower():
            outline_parts.append("2. Navigate to **Settings → Code security and analysis**")
            outline_parts.append("3. Review the new security features")
        else:
            outline_parts.append("2. Navigate to the relevant settings or feature area")
            outline_parts.append("3. Explore the new functionality")

    outline_parts.append(f"4. Review the feature behavior")
    outline_parts.append("")

    # Docs link
    if entry.docs_url:
        outline_parts.append(f"**Documentation:** [View full docs]({entry.docs_url})")
        outline_parts.append("")

    # Changelog link
    outline_parts.append(f"**Changelog:** [Read more]({entry.url})")

    return "\n".join(outline_parts)


def enrich_entries_with_demo_outlines(entries: list[ChangelogEntry]) -> list[ChangelogEntry]:
    """Add demo outlines to release entries."""
    for entry in entries:
        if entry.category == "Release":
            # Extract docs URL
            entry.docs_url = extract_docs_url(entry.content_html)

            # Try to extract navigation path from docs
            if entry.docs_url:
                entry.navigation_path = extract_navigation_path(entry.docs_url)

            # Generate demo outline
            entry.demo_outline = generate_demo_outline(entry)

    return entries


def entries_to_dict(entries: list[ChangelogEntry]) -> list[dict]:
    """Convert entries to dictionaries for JSON serialization and templating."""
    return [
        {
            "title": e.title,
            "url": e.url,
            "published": e.published,
            "summary": BeautifulSoup(e.summary, "html.parser").get_text().strip() if e.summary else "",
            "content_html": e.content_html,
            "category": e.category,
            "labels": e.labels,
            "demo_outline": e.demo_outline,
            "navigation_path": e.navigation_path,
            "docs_url": e.docs_url,
        }
        for e in entries
    ]
