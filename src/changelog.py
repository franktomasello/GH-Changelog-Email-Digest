"""
Changelog fetching, parsing, categorization, and docs-link resolution.
"""

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Optional
from urllib.parse import urljoin, urlsplit, urlunsplit, parse_qsl, urlencode
from zoneinfo import ZoneInfo

import feedparser
import requests
from bs4 import BeautifulSoup

CHANGELOG_FEED_URL = "https://github.blog/changelog/feed/"
DOCS_BASE_URL = "https://docs.github.com"

# Enterprise documentation base paths
GHEC_DOCS_PREFIX = "/en/enterprise-cloud@latest/"
GHES_DOCS_PREFIX = "/en/enterprise-server@"

# Pacific Time Zone
PACIFIC_TZ = ZoneInfo("America/Los_Angeles")

# Only include entries from the past 7 days
MAX_AGE_DAYS = 7

# Shared session for docs-resolution calls. A single run makes many requests to
# the same host (docs.github.com); reusing one connection (keep-alive) avoids a
# fresh TCP+TLS handshake per call. Behavior is otherwise identical to requests.*.
_DOCS_SESSION = requests.Session()

# Human-verified docs-link overrides, keyed by changelog entry URL. Consulted
# before any automated resolution so audited entries always link to the proven
# page. A null value means "show no docs link". See data/docs_overrides.json.
_OVERRIDES_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "docs_overrides.json")
_NO_OVERRIDE = object()  # sentinel: entry isn't in the override map -> resolve normally
_overrides_cache = None


def docs_override(entry_url: str):
    """Return the verified docs URL for an entry (a str), None to suppress the
    link, or the _NO_OVERRIDE sentinel when the entry isn't listed."""
    global _overrides_cache
    if _overrides_cache is None:
        try:
            with open(_OVERRIDES_FILE) as f:
                _overrides_cache = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            _overrides_cache = {}
    if entry_url in _overrides_cache:
        return _overrides_cache[entry_url]
    return _NO_OVERRIDE


def convert_to_pst(date_string: str) -> str:
    """Convert RSS date string to Pacific Time formatted string."""
    try:
        # Parse the RSS date format (e.g., "Thu, 15 Jan 2026 21:57:44 +0000")
        dt = parsedate_to_datetime(date_string)
        # Convert to Pacific Time
        dt_pst = dt.astimezone(PACIFIC_TZ)
        # Format nicely: "Jan 15, 2026"
        return dt_pst.strftime("%b %-d, %Y")
    except Exception:
        # If parsing fails, return original
        return date_string


@dataclass
class ChangelogEntry:
    """Represents a single changelog entry."""
    title: str
    url: str
    published: str
    published_dt: datetime  # Original datetime for filtering
    summary: str
    content_html: str
    category: str  # "Release", "Improvement", or "Retired"
    labels: list = field(default_factory=list)
    docs_url: Optional[str] = None
    # Populated during enrichment (see enrich_entries).
    detailed_summary: str = ""
    key_features: list = field(default_factory=list)


def _capitalize_label(label: str) -> str:
    """Capitalize each word of a feed label for display ("collaboration tools"
    -> "Collaboration Tools"), preserving existing uppercase ("API" stays "API")."""
    return " ".join(w[:1].upper() + w[1:] for w in label.split(" "))


def fetch_changelog(max_age_days: int = MAX_AGE_DAYS) -> list[ChangelogEntry]:
    """Fetch and parse the GitHub changelog RSS feed.
    
    Only returns entries from the past max_age_days (default 7 days).

    Raises on a feed outage. feedparser.parse() given a URL does its own
    unbounded fetch and never raises on a network/HTTP failure — it just returns
    an empty feed — which would make a real outage indistinguishable from
    "nothing new today" and let the daily run pass silently green. Fetching the
    bytes ourselves with a timeout + raise_for_status turns an outage into a loud
    failure, while a genuinely empty (but reachable) feed still returns [].
    """
    response = requests.get(CHANGELOG_FEED_URL, timeout=10)
    response.raise_for_status()
    feed = feedparser.parse(response.content)
    if feed.bozo and not feed.entries:
        # Reachable but unparseable and nothing recovered — fail rather than
        # silently treating a broken feed as an empty one.
        raise RuntimeError(
            f"Changelog feed could not be parsed: {feed.get('bozo_exception')!r}"
        )
    entries = []

    # Calculate cutoff date (entries older than this are excluded)
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=max_age_days)

    for item in feed.entries:
        # Parse the publication date
        published_str = item.get("published", "")
        try:
            published_dt = parsedate_to_datetime(published_str)
        except Exception:
            # If we can't parse the date, skip this entry
            continue

        # Skip entries with a missing or timezone-naive date. Comparing a naive
        # datetime against the aware cutoff below raises TypeError, which (since
        # this loop is unguarded) would abort the entire run and skip the day's
        # digest. GitHub's feed is always tz-aware, so this only guards against a
        # malformed or changed feed.
        if published_dt is None or published_dt.tzinfo is None:
            continue

        # Skip entries older than the cutoff
        if published_dt < cutoff_date:
            continue
        
        # Extract category type (Release, Improvement, Retired)
        category = "Improvement"  # Default
        labels = []

        for tag in item.get("tags", []):
            if tag.get("scheme") == "changelog-type":
                category = tag.get("term", "Improvement")
            elif tag.get("scheme") == "changelog-label":
                label = tag.get("term", "")
                if label:
                    labels.append(_capitalize_label(label))

        # Get content (prefer full content over summary)
        content_html = ""
        if hasattr(item, "content") and item.content:
            content_html = item.content[0].get("value", "")
        elif hasattr(item, "summary"):
            content_html = item.summary

        entry = ChangelogEntry(
            title=item.get("title", ""),
            url=item.get("link", ""),
            published=convert_to_pst(published_str),
            published_dt=published_dt,
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
        # Match case-insensitively: the term comes verbatim from the feed's
        # changelog-type tag, so a casing/wording change ("release", "Deprecated")
        # would otherwise silently land in Improvements.
        kind = (entry.category or "").strip().lower()
        if kind == "release":
            categorized["releases"].append(entry)
        elif kind in ("retired", "deprecated", "retirement"):
            categorized["retirements"].append(entry)
        else:
            if kind not in ("improvement", ""):
                # Surface an unrecognized changelog-type so a feed-schema change
                # is visible in CI logs rather than silently misclassified
                # (consistent with the docs-coverage logging in main.py).
                print(f"   ⚠️  Unrecognized changelog-type '{entry.category}' "
                      f"— treating as Improvement: {entry.title[:60]}")
            categorized["improvements"].append(entry)

    return categorized


def is_enterprise_docs_url(url: str) -> bool:
    """
    Check if a URL points to GitHub Enterprise Cloud or Enterprise Server docs.
    
    Valid patterns:
      - docs.github.com/en/enterprise-cloud@latest/...
      - docs.github.com/en/enterprise-server@X.Y/...
      - docs.github.com/en/enterprise-server@latest/...
    """
    if "docs.github.com" not in url:
        return False
    return GHEC_DOCS_PREFIX in url or GHES_DOCS_PREFIX in url


def convert_to_enterprise_docs_urls(url: str) -> list[str]:
    """
    Attempt to convert a generic docs.github.com URL to its
    Enterprise Cloud and Enterprise Server equivalents.
    
    For example:
      docs.github.com/en/repositories/...
    becomes:
      docs.github.com/en/enterprise-cloud@latest/repositories/...
    
    Returns a list of candidate URLs (GHEC first, then GHES).
    Returns an empty list if the URL isn't a docs.github.com URL.
    """
    if "docs.github.com" not in url:
        return []
    
    # Already an enterprise URL — return as-is
    if is_enterprise_docs_url(url):
        return [url]
    
    # Convert /en/topic/... → /en/enterprise-cloud@latest/topic/...
    candidates = []
    if "/en/" in url:
        ghec = url.replace("/en/", f"/en/enterprise-cloud@latest/", 1)
        ghes = url.replace("/en/", f"/en/enterprise-server@latest/", 1)
        candidates = [ghec, ghes]
    
    return candidates


def verify_enterprise_url_exists(url: str) -> bool:
    """Check that an enterprise docs URL actually exists (returns 200)."""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
            'Accept': 'text/html',
        }
        response = _DOCS_SESSION.head(url, headers=headers, timeout=10, allow_redirects=True)
        return response.status_code == 200
    except Exception:
        return False


def search_github_docs(query: str, prefer_enterprise: bool = True) -> Optional[str]:
    """
    Search GitHub documentation for the most relevant page.

    With ``prefer_enterprise=True`` only Enterprise Cloud/Server docs URLs are
    returned (searched against the enterprise-cloud docs); with False the
    general docs are searched and the first article result is returned.
    ``query`` may include the entry summary for richer context.
    """
    try:
        # Clean up the query - extract key terms
        # Remove common words that don't help search
        stop_words = {'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 
                      'being', 'have', 'has', 'had', 'do', 'does', 'did', 'will',
                      'would', 'could', 'should', 'may', 'might', 'must', 'shall',
                      'can', 'need', 'dare', 'ought', 'used', 'to', 'of', 'in',
                      'for', 'on', 'with', 'at', 'by', 'from', 'as', 'into',
                      'through', 'during', 'before', 'after', 'above', 'below',
                      'between', 'under', 'again', 'further', 'then', 'once',
                      'now', 'generally', 'available', 'new', 'and', 'or', 'but',
                      'if', 'because', 'until', 'while', 'this', 'that', 'these',
                      'those', 'am', 'it', 'its', "it's", 'they', 'them', 'their',
                      'what', 'which', 'who', 'whom', 'where', 'when', 'why', 'how',
                      'all', 'each', 'every', 'both', 'few', 'more', 'most', 'other',
                      'some', 'such', 'no', 'nor', 'not', 'only', 'own', 'same',
                      'so', 'than', 'too', 'very', 'just', 'also'}
        
        # Extract meaningful words from query
        words = re.findall(r'\b[a-zA-Z][a-zA-Z0-9+#-]*\b', query.lower())
        keywords = [w for w in words if w not in stop_words and len(w) > 2]
        
        if not keywords:
            return None
        
        # Build search query
        search_query = " ".join(keywords[:6])  # Limit to top 6 keywords
        
        # Search enterprise docs (default) or the general docs.
        if prefer_enterprise:
            search_url = f"https://docs.github.com/en/enterprise-cloud@latest/search?query={requests.utils.quote(search_query)}"
        else:
            search_url = f"https://docs.github.com/en/search?query={requests.utils.quote(search_query)}"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        }
        
        response = _DOCS_SESSION.get(search_url, headers=headers, timeout=10)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, "html.parser")
        
        # Look for search result links. Prefer a non-REST page — REST API
        # reference pages match keywords but document the API, not the feature;
        # keep the first REST hit only as a last resort.
        rest_fallback = None
        for link in soup.find_all("a", href=True):
            href = link["href"]
            skip = ["/search", "/site-policy", "/get-started/learning-about-github"]
            if not (href.startswith("/en/") and not any(x in href for x in skip)):
                continue
            full_url = f"https://docs.github.com{href}"
            if prefer_enterprise and not is_enterprise_docs_url(full_url):
                continue
            if "/rest/" in href or "apiversion=" in href:
                rest_fallback = rest_fallback or full_url
                continue
            return full_url

        return rest_fallback
        
    except Exception as e:
        print(f"  ⚠️  Docs search failed for '{query[:50]}...': {e}")
        return None


def validate_docs_url(url: str, title: str, summary: str = "", strict: bool = False) -> bool:
    """
    Validate that a documentation URL is actually relevant to the changelog entry.
    Fetches the page and checks if its content meaningfully relates to the entry.
    
    Args:
        url: The candidate documentation URL.
        title: The changelog entry title.
        summary: The changelog entry summary text.
        strict: If True, requires a higher match threshold (used for search results
                which are less trustworthy than embedded links).
    
    Returns True only if the page content matches the entry topic.
    """
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        }
        response = _DOCS_SESSION.get(url, headers=headers, timeout=10, allow_redirects=True)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        page_text = soup.get_text(separator=" ", strip=True).lower()
        page_title = (soup.title.get_text().lower() if soup.title else "")

        # Extract meaningful keywords from the changelog entry title
        stop_words = {
            'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been',
            'being', 'have', 'has', 'had', 'do', 'does', 'did', 'will',
            'would', 'could', 'should', 'may', 'might', 'must', 'shall',
            'can', 'need', 'to', 'of', 'in', 'for', 'on', 'with', 'at',
            'by', 'from', 'as', 'into', 'through', 'during', 'before',
            'after', 'now', 'generally', 'available', 'new', 'and', 'or',
            'but', 'if', 'this', 'that', 'these', 'those', 'it', 'its',
            'all', 'each', 'every', 'both', 'few', 'more', 'most', 'other',
            'some', 'such', 'no', 'not', 'only', 'own', 'same', 'so',
            'than', 'too', 'very', 'just', 'also', 'github', 'support',
            'supports', 'update', 'updates', 'feature', 'features',
            'track', 'additional', 'changes', 'are', 'preview', 'technical',
        }

        title_words = re.findall(r'\b[a-zA-Z][a-zA-Z0-9+#.-]*\b', title.lower())
        keywords = [w for w in title_words if w not in stop_words and len(w) > 2]

        if not keywords:
            return False

        # Check how many keywords from the entry title appear in the page content
        matches = sum(1 for kw in keywords if kw in page_text)
        match_ratio = matches / len(keywords) if keywords else 0

        # Also check page title for keyword overlap
        title_matches = sum(1 for kw in keywords if kw in page_title)
        title_ratio = title_matches / len(keywords) if keywords else 0

        # Thresholds depend on source trustworthiness:
        # - Embedded links (strict=False): 30% body OR 20% page title.
        #   These are GitHub's own hand-picked links in the changelog post, so
        #   they're authoritative — a light relevance check is enough to catch
        #   the occasional generic/homepage link without dropping good ones.
        # - Search results (strict=True): 60% body OR 50% page title.
        #   Search results are often tangential; require strong evidence.
        if strict:
            body_threshold = 0.60
            title_threshold = 0.50
        else:
            body_threshold = 0.30
            title_threshold = 0.20

        if match_ratio >= body_threshold or title_ratio >= title_threshold:
            return True

        return False

    except Exception as e:
        print(f"  ⚠️  Could not validate docs URL '{url}': {e}")
        return False


def search_docs_for_release(title: str, content_html: str = "", summary: str = "") -> Optional[str]:
    """
    Find the most accurate documentation URL for a changelog entry.

    Accuracy-first, with an Enterprise lean. Priority order:
      1. The most relevant docs.github.com link the changelog itself embeds
         (the entry's own canonical reference):
           a. if it's already an Enterprise URL, use it;
           b. if it's a general URL, prefer a verified Enterprise equivalent;
           c. otherwise use the accurate general link rather than discarding it.
      2. Search the docs using the title AND summary — Enterprise docs first,
         then general docs — each strictly validated for relevance.

    Every candidate is fetched and checked against the entry's keywords before
    it's used. Returns None if nothing validates, so the template shows no docs
    link rather than a wrong one.
    """
    keywords = _relevance_keywords(f"{title} {summary}")

    # 0) When the LLM features are enabled, let the model choose the canonical
    #    docs page — far more accurate than keyword overlap for cases where the
    #    right page shares few words with the title (e.g. an update about NOT
    #    needing a PAT belongs on the GITHUB_TOKEN page, not "managing PATs").
    #    Verify the pick resolves (200) before trusting it, and prefer its
    #    Enterprise equivalent. Falls through to the heuristic if disabled, if
    #    the model declines, or if the pick doesn't resolve.
    if _llm_enabled():
        pick = llm_pick_docs_url(title, content_html, summary)
        if pick:
            pick = _strip_tracking(pick)
            if not is_enterprise_docs_url(pick):
                for candidate in convert_to_enterprise_docs_urls(pick):
                    if verify_enterprise_url_exists(candidate):
                        print(f"  ✅ LLM-selected (enterprise): {candidate}")
                        return candidate
            if verify_enterprise_url_exists(pick):
                print(f"  ✅ LLM-selected: {pick}")
                return pick
            print(f"  ⚠️  LLM-selected URL did not resolve, falling back: {pick}")

    # 1) The changelog's own best embedded docs link — the canonical source.
    embedded_url = extract_best_docs_url(content_html, keywords) if content_html else None
    if embedded_url:
        embedded_url = _strip_tracking(embedded_url)
        # 1a. Already an Enterprise docs URL.
        if is_enterprise_docs_url(embedded_url):
            if validate_docs_url(embedded_url, title, summary, strict=False):
                return embedded_url
            print(f"  ⚠️  Embedded enterprise docs URL rejected (not relevant): {embedded_url}")
        else:
            # 1b. Prefer a verified Enterprise version of the same page.
            for candidate in convert_to_enterprise_docs_urls(embedded_url):
                if verify_enterprise_url_exists(candidate) and validate_docs_url(candidate, title, summary, strict=False):
                    print(f"  ✅ Using enterprise equivalent: {candidate}")
                    return candidate
            # 1c. Otherwise use the accurate general docs link itself.
            if validate_docs_url(embedded_url, title, summary, strict=False):
                print(f"  ✅ Using changelog docs link: {embedded_url}")
                return embedded_url
            print(f"  ⚠️  Embedded docs URL rejected (not relevant): {embedded_url}")

    # 2) Search the docs with full context (title + summary).
    query = f"{title} {summary}".strip()
    enterprise_hit = search_github_docs(query, prefer_enterprise=True)
    if enterprise_hit and validate_docs_url(enterprise_hit, title, summary, strict=True):
        return enterprise_hit
    general_hit = search_github_docs(query, prefer_enterprise=False)
    if general_hit and validate_docs_url(general_hit, title, summary, strict=True):
        return general_hit

    # Nothing verified — the template omits the docs link entirely.
    print(f"  ❌ No verified docs URL found for: {title[:60]}")
    return None


def _strip_tracking(url: str) -> str:
    """Drop marketing/tracking query params (utm_*, ref, source) from a docs
    URL so the link is the clean canonical page. Keeps any #fragment."""
    try:
        parts = urlsplit(url)
        if not parts.query:
            return url
        kept = [
            (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if not k.lower().startswith("utm_") and k.lower() not in ("ref", "source")
        ]
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), parts.fragment))
    except Exception:
        return url


def _relevance_keywords(text: str) -> list[str]:
    """Meaningful keywords from text, for relevance scoring and docs search."""
    stop = {
        'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
        'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
        'should', 'may', 'might', 'must', 'shall', 'can', 'to', 'of', 'in',
        'for', 'on', 'with', 'at', 'by', 'from', 'as', 'into', 'now',
        'generally', 'available', 'new', 'and', 'or', 'but', 'if', 'this',
        'that', 'these', 'those', 'it', 'its', 'all', 'each', 'every', 'both',
        'more', 'most', 'other', 'some', 'such', 'no', 'not', 'only', 'own',
        'same', 'so', 'than', 'too', 'very', 'just', 'also', 'github', 'update',
        'updates', 'feature', 'features', 'support', 'supports', 'preview',
    }
    words = re.findall(r'\b[a-zA-Z][a-zA-Z0-9+#.-]*\b', text.lower())
    return [w for w in words if w not in stop and len(w) > 2]


def extract_best_docs_url(content_html: str, keywords: list[str]) -> Optional[str]:
    """
    Pick the docs.github.com link in the changelog content that best matches
    the entry (by keyword overlap with the URL path). Enterprise links win
    ties. Returns None when the content has no docs link — this is the entry's
    own canonical reference and the most accurate source available.
    """
    if not content_html:
        return None
    soup = BeautifulSoup(content_html, "html.parser")
    # Distinct keywords only — counting duplicates lets a word repeated in the
    # summary multiply a URL's score and drown out the ranking signals below.
    unique_keywords = set(keywords)
    best = None
    best_score = -1.0
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if not href or href.startswith("#") or "docs.github.com" not in href:
            continue
        path = href.lower()
        score = float(sum(1 for kw in unique_keywords if kw in path))
        if is_enterprise_docs_url(href):
            score += 0.5  # tie-break toward enterprise docs
        # REST API reference pages document the API surface, not the feature a
        # changelog entry announces — they're rarely the right "Read the docs"
        # target (posts usually link them only to show how to query data).
        if "/rest/" in path or "apiversion=" in path:
            score -= 3.0
        # A release-notes page is the right target for a whole-version entry
        # (e.g. "GHES 3.21 is now generally available"), which otherwise has no
        # single feature page that represents it.
        if "/release-notes" in path:
            score += 2.0
        if score > best_score:
            best_score = score
            best = href
    return best


def _clean_html_to_text(html: str) -> str:
    """Strip HTML to clean text, removing boilerplate and normalizing whitespace."""
    soup = BeautifulSoup(html, "html.parser")
    for element in soup(["script", "style", "table", "h1", "h2", "h3", "h4", "h5", "h6"]):
        element.decompose()
    text = soup.get_text(separator=" ", strip=True)

    boilerplate_patterns = [
        r"The post .+ appeared first on The GitHub Blog\.",
        r"The post .+ appeared first on GitHub Blog\.",
        r"appeared first on The GitHub Blog\.",
        r"appeared first on GitHub Blog\.",
        r"Learn more\s*$",
    ]
    for pattern in boilerplate_patterns:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE).strip()

    # Normalize whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    # Fix spaces before punctuation (artifact of stripping inline links/tags)
    text = re.sub(r'\s+([,;:.!?])', r'\1', text)
    # Fix double punctuation like .… or ..
    text = re.sub(r'\.…', '.', text)
    text = re.sub(r'\.\.+', '.', text)
    text = re.sub(r'…\.', '.', text)
    return text


# Filler/preamble patterns that don't add value in a summary
_FILLER_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r"^(we('re|are)\s+)?(excited|happy|pleased|thrilled|proud)\s+to\s+announce",
        r"^(we('re|are)\s+)?(excited|happy|pleased|thrilled|proud)\s+to\s+share",
        r"^(we('re|are)\s+)?(excited|happy|pleased|thrilled|proud)\s+to\s+introduce",
        r"^read more below",
        r"^here'?s what'?s (new|changed|coming|happening)",
        r"^check (it )?out",
        r"^what'?s new\??$",
        r"^overview:?$",
        r"^introduction:?$",
        r"^summary:?$",
        r"^in this (update|release|post)",
        r"^today,?\s+we('re|are)\s+(releasing|launching|announcing|introducing|shipping)",
        r"^(starting|beginning)\s+(today|now),?\s+",
    ]
]


def _is_filler_sentence(sentence: str) -> bool:
    """Return True if the sentence is preamble/filler that doesn't describe the feature."""
    return any(p.search(sentence.strip()) for p in _FILLER_PATTERNS)


def _normalize_summary_text(text: str) -> str:
    """Ensure the summary ends cleanly with proper punctuation."""
    text = text.strip()
    if not text:
        return text
    # Clean up punctuation artifacts
    text = re.sub(r'\.…', '.', text)
    text = re.sub(r'…\.', '.', text)
    text = re.sub(r'\.\.+', '.', text)
    # Remove trailing ellipsis (incomplete thought)
    text = re.sub(r'\s*…\s*$', '.', text)
    # Remove trailing fragments after the last sentence-ending punctuation
    match = re.match(r'^(.*[.!?])\s+\S[^.!?]*$', text, re.DOTALL)
    if match:
        text = match.group(1).strip()
    # Ensure it ends with punctuation
    if text and text[-1] not in '.!?':
        text += '.'
    return text


def _echoes_title(sentence: str, title: str) -> bool:
    """True if a sentence essentially just restates the title.

    Conservative: only fires when the sentence adds at most one content word
    beyond the title and isn't much longer than it — so a substantive opener
    that merely shares some title words (e.g. "Two new GitHub-hosted runner
    images for GitHub Actions are now available…") is kept.
    """
    def content_words(s: str) -> set:
        return {w for w in re.findall(r"[a-z0-9.]+", s.lower()) if len(w) > 3}

    sentence_words = content_words(sentence)
    title_words = content_words(title)
    if not sentence_words or not title_words:
        return False
    novel = sentence_words - title_words
    return len(novel) <= 1 and len(sentence) <= len(title) + 25


def extract_detailed_summary(entry: ChangelogEntry) -> str:
    """
    Extract a concise, well-formed summary from the changelog entry.
    Skips filler/preamble sentences and focuses on what the feature does.
    """
    if entry.content_html:
        text = _clean_html_to_text(entry.content_html)

        # Meaningful sentences: drop short fragments and filler/preamble.
        sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', text)]
        candidates = [s for s in sentences if len(s) >= 15 and not _is_filler_sentence(s)]

        # Drop a leading sentence that merely restates the title (e.g. "X is now
        # in public preview.") when there's substantive text after it, so the
        # summary leads with what shipped rather than echoing the headline.
        if len(candidates) > 1 and _echoes_title(candidates[0], entry.title):
            candidates = candidates[1:]

        summary_sentences = []
        char_count = 0
        for sentence in candidates:
            if char_count + len(sentence) <= 350:
                summary_sentences.append(sentence)
                char_count += len(sentence)
            else:
                break

        if summary_sentences:
            return _normalize_summary_text(" ".join(summary_sentences))

    # Fall back to RSS summary
    if entry.summary:
        text = _clean_html_to_text(entry.summary)
        if text:
            return _normalize_summary_text(text)

    return ""


# Function words that read as dangling if a condensed bullet ends on them.
_TRAILING_STOPWORDS = {
    "and", "or", "but", "with", "to", "the", "a", "an", "of", "for", "in", "on",
    "by", "that", "as", "at", "from", "into", "so", "which", "while", "when",
    "where", "this", "giving", "allowing", "letting", "including", "such",
    "its", "their", "your", "our", "his", "her", "my", "it", "they", "you", "we",
}


def _condense_feature(text: str, limit: int = 140) -> str:
    """Reduce an over-long list item to one clean clause for a bullet.

    Long <li>s used to be discarded entirely, leaving information-rich entries
    (e.g. a GHES release) with no features at all. Keep the first sentence, or
    failing that the leading clause before a comma, trimming any dangling
    fragment left by the cut.
    """
    text = re.split(r'(?<=[.!?])\s+', text.strip())[0].strip()
    if len(text) > limit:
        head = text[:limit]
        # Prefer cutting at the last comma; otherwise the last word boundary.
        text = head.rsplit(",", 1)[0] if "," in head else head.rsplit(" ", 1)[0]
    # Drop a trailing unclosed parenthetical left by the cut, e.g. "... jobs (i.e".
    text = re.sub(r'\s*\([^)]*$', '', text)
    words = text.rstrip(".,;:").split()
    while words and words[-1].lower().strip(".,;:") in _TRAILING_STOPWORDS:
        words.pop()
    return " ".join(words)


# Lowercase CLI/command tokens that must never be title-cased when they lead a
# feature bullet (e.g. "gh discussion list" must not become "Gh discussion list").
_COMMAND_PREFIXES = {
    "gh", "git", "npm", "npx", "pip", "pipx", "pnpm", "yarn", "curl", "wget",
    "docker", "kubectl", "brew", "dotnet", "cargo", "bundle", "gem", "mvn",
    "gradle", "terraform", "aws", "gcloud", "ssh", "scp", "psql",
}


def _normalize_feature_text(text: str) -> str:
    """Clean and normalize a single feature bullet point."""
    text = re.sub(r'\s+', ' ', text).strip()
    # Fix spaces before punctuation
    text = re.sub(r'\s+([,;:.!?])', r'\1', text)
    # Strip leading bullets, dashes, or numbering
    text = re.sub(r'^[\-\u2022\u2013\u2014\*]\s*', '', text)
    text = re.sub(r'^\d+[.)]\s*', '', text)
    # Strip trailing colons (leftover from headers used as list items)
    text = re.sub(r':+\s*$', '', text)
    # Replace underscores with spaces in display text (e.g., Find_symbol -> Find symbol)
    # but not in code-like strings (e.g., npm install, pip install, dotnet add)
    if not any(kw in text.lower() for kw in ['install', 'import', 'go get', 'dotnet', 'pip', 'npm', 'require']):
        text = re.sub(r'(?<=[a-zA-Z])_(?=[a-zA-Z])', ' ', text)
    # Capitalize the first letter — but skip code tokens (.NET, @handle) and CLI
    # command names (gh, git, npm, ...), where the lowercase form is intentional.
    first_word = text.split(maxsplit=1)[0].lower() if text else ""
    if (text and text[0].islower()
            and not text.startswith(('.', '@'))
            and first_word not in _COMMAND_PREFIXES):
        text = text[0].upper() + text[1:]
    # Bullet points never need trailing periods — keep them consistent
    text = text.rstrip('.')
    return text


# Bullets that aren't demoable capabilities — pricing/licensing lines and pure
# "nothing changes" reassurances/conditions. Filtered so the "what to show" list
# stays on-goal for a Solutions Engineer.
_OFFGOAL_BULLET = re.compile(
    r'(\$\s?\d'
    r'|\bper\s+(active\s+)?(committer|user|seat|month|year)\b'
    r'|\b(paid product|usage-based billing|pricing)\b'
    r'|^if\s+you\b'
    r'|\bnothing\s+(changes|will\s+change)\b'
    r'|\bno\s+(action|changes?)\s+(is|are|needed|required)\b'
    r')', re.IGNORECASE)


def _is_offgoal_bullet(text: str) -> bool:
    """True for bullets that aren't demoable capabilities (pricing/licensing or
    pure 'nothing changes' reassurances) and should be dropped."""
    return bool(_OFFGOAL_BULLET.search(text))


def extract_key_features(entry: ChangelogEntry) -> list[str]:
    """
    Extract key features relevant to SE demos.
    Focuses on capabilities, not implementation details.
    Returns up to 4 clean, deduplicated bullet points.
    """
    title_lower = entry.title.lower()
    natural_li = []     # list items that fit a bullet as-is (complete sentences)
    condensed_li = []   # over-long items reduced to their first clause
    bold_features = []

    if entry.content_html:
        soup = BeautifulSoup(entry.content_html, "html.parser")

        # List items are the reliable source of demoable capabilities.
        for li in soup.find_all("li"):
            raw = li.get_text().strip()
            if 15 < len(raw) < 150:
                text = _normalize_feature_text(raw)
                if text:
                    natural_li.append(text)
            elif len(raw) >= 150:
                # Previously dropped; condense to its first clause so detailed
                # entries still surface features — but rank these below complete
                # items so a clean short bullet always wins.
                condensed = _condense_feature(raw)
                if 15 < len(condensed) < 150:
                    text = _normalize_feature_text(condensed)
                    if text:
                        condensed_li.append(text)

        # Bold/strong text is usually a section heading, not a feature, so use it
        # only as a fallback when the post has no usable list items — and require
        # real length so one-word headings ("Security") don't become bullets.
        for strong in soup.find_all(["strong", "b"]):
            text = strong.get_text().strip()
            if 15 < len(text) < 80:
                text = _normalize_feature_text(text)
                if text:
                    bold_features.append(text)

    # Prefer complete list items; fill with condensed-long items; fall back to
    # bold headings only when there are no list items at all.
    li_features = natural_li + condensed_li
    features = li_features if li_features else bold_features

    # Deduplicate: remove features that are substrings of the title or of each other
    deduped = []
    for f in features:
        f_lower = f.lower()
        # Skip if it's essentially the title restated
        if f_lower in title_lower or title_lower in f_lower:
            continue
        # Skip pricing/licensing lines and pure reassurances — not demoable.
        if _is_offgoal_bullet(f):
            continue
        # Skip if it's a substring of an already-kept feature
        if any(f_lower in kept.lower() or kept.lower() in f_lower for kept in deduped):
            continue
        deduped.append(f)

    return deduped[:4]


# Default model for the optional LLM features — a small, fast, inexpensive model
# is plenty for a one-sentence summary or a docs-link choice. Override with
# DIGEST_LLM_MODEL.
_LLM_MODEL = "claude-haiku-4-5-20251001"

_LLM_SUMMARY_SYSTEM = (
    "You write one concise, factual sentence (two at most) describing what a "
    "GitHub changelog update actually does, for GitHub Solutions Engineers and "
    "Sales Reps. Lead with the capability. No marketing language, no preamble "
    "(no 'We're excited'), no first person, present tense. Output only the summary."
)

_LLM_DOCS_SYSTEM = (
    "You pick the single best GitHub documentation page for a changelog update's "
    "'Read the docs' link — the canonical page that documents what shipped, where "
    "a reader would want to land. Output ONLY a bare URL on docs.github.com, or the "
    "word NONE if no good docs page exists. No prose, no markdown."
)

_LLM_FEATURES_SYSTEM = (
    "You list the concrete, demoable capabilities in a GitHub changelog update — "
    "the specific things a GitHub Solutions Engineer would actually show in a demo: "
    "new commands, UI actions, settings, or capabilities. Output up to 4, one per "
    "line, each a short phrase that leads with the capability. No numbering, no "
    "markdown, no preamble. Exclude pricing, licensing, version requirements, dates, "
    "and vague statements ('new capabilities available'). If the update has no "
    "demoable surface (e.g. a pure retirement, policy, or billing change), output "
    "nothing at all."
)


def _llm_enabled() -> bool:
    """True when the optional LLM features are explicitly turned on (off by
    default, so the daily cron is unchanged until opted in)."""
    flag = (os.environ.get("DIGEST_LLM")
            or os.environ.get("DIGEST_LLM_SUMMARIES") or "").strip().lower()
    return flag in ("1", "true", "yes") and bool(os.environ.get("ANTHROPIC_API_KEY"))


def _llm_call(system: str, user: str, max_tokens: int) -> Optional[str]:
    """Single Anthropic call; returns the text or None on any failure (so callers
    fall back gracefully). Imports anthropic lazily — it's an optional dependency."""
    try:
        import anthropic
    except ImportError:
        return None
    try:
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model=os.environ.get("DIGEST_LLM_MODEL", _LLM_MODEL),
            max_tokens=max_tokens,
            temperature=0,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(
            getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text"
        ).strip()
    except Exception as e:
        print(f"  ⚠️  LLM call failed ({e})")
        return None


def llm_summary(entry: ChangelogEntry) -> Optional[str]:
    """Optional, polished summary via the Anthropic API.

    Returns None — so the caller falls back to the heuristic extract_detailed_summary —
    unless the LLM features are enabled AND the call succeeds. To enable:
      * pip install anthropic
      * set DIGEST_LLM=1 (or DIGEST_LLM_SUMMARIES=1) and ANTHROPIC_API_KEY
    """
    if not _llm_enabled():
        return None
    source = _clean_html_to_text(entry.content_html or entry.summary or "")[:1500]
    if not source:
        return None
    text = _llm_call(_LLM_SUMMARY_SYSTEM, f"Title: {entry.title}\n\nChangelog text:\n{source}", 120)
    return _normalize_summary_text(text) if text else None


def llm_key_features(title: str, content_html: str = "", summary: str = "") -> Optional[list[str]]:
    """Optional, goal-tuned key-feature bullets via the Anthropic API.

    The heuristic extractor just scrapes the post's list items, so it leaks
    pricing/requirements and produces nothing for prose-only posts. When enabled,
    the model picks the concrete, demoable capabilities instead.

    Returns None when disabled or the call fails (caller falls back to the
    heuristic). Returns a list otherwise — possibly EMPTY, which is meaningful:
    the model judged the update has no demoable surface (e.g. a pure retirement),
    so the caller should show no bullets rather than fall back.
    """
    if not _llm_enabled():
        return None
    context = _clean_html_to_text(content_html or summary or "")[:1800]
    if not context:
        return None
    text = _llm_call(_LLM_FEATURES_SYSTEM, f"Title: {title}\n\nUpdate:\n{context}", 220)
    if text is None:
        return None  # call failed -> let the caller use the heuristic
    bullets = []
    for line in text.splitlines():
        line = re.sub(r'^[\-\*•–—\d.\)\s]+', '', line).strip()
        if len(line) > 2:
            cleaned = _normalize_feature_text(line)
            if cleaned:
                bullets.append(cleaned)
    return bullets[:4]


def _all_embedded_docs_links(content_html: str) -> list[str]:
    """Every distinct docs.github.com link the post embeds (tracking stripped)."""
    out = []
    if content_html:
        for a in BeautifulSoup(content_html, "html.parser").find_all("a", href=True):
            href = a["href"]
            if "docs.github.com" in href and not href.startswith("#"):
                clean = _strip_tracking(href)
                if clean not in out:
                    out.append(clean)
    return out


def llm_pick_docs_url(title: str, content_html: str = "", summary: str = "") -> Optional[str]:
    """Optional, high-accuracy docs-link selection via the Anthropic API.

    Keyword overlap can't tell that "managing PATs" is the wrong page for an
    update about *no longer needing* a PAT. When enabled, ask the model for the
    canonical docs page (it knows GitHub's docs structure and the post's own
    embedded links); the caller then verifies the URL returns 200 before using
    it, so a bad pick can never produce a dead link. Returns None when disabled,
    on failure, or when the model judges no good page exists.
    """
    if not _llm_enabled():
        return None
    context = _clean_html_to_text(content_html or summary or "")[:1200]
    embedded = _all_embedded_docs_links(content_html)
    user = (
        f"Changelog title: {title}\n"
        f"What it covers: {context}\n"
        f"Docs links the post itself embeds: {embedded or 'none'}\n\n"
        "Give the single best docs.github.com 'Read the docs' URL for THIS update. "
        "Prefer the Enterprise Cloud path (/en/enterprise-cloud@latest/...) when an "
        "equivalent page exists. Use an embedded link only if it is genuinely the "
        "page about what shipped (not a tangential or analogy link). For a whole-"
        "version release, prefer its release-notes page. Reply NONE if no good page exists."
    )
    text = _llm_call(_LLM_DOCS_SYSTEM, user, 200)
    if not text:
        return None
    # Take the first docs.github.com token; ignore NONE / prose.
    for token in text.split():
        token = token.strip().strip('<>"\'.,)')
        if token.startswith("https://docs.github.com"):
            return token
    return None


def enrich_entries(entries: list[ChangelogEntry]) -> list[ChangelogEntry]:
    """Attach a cleaned summary, key features, and a verified docs link to each entry."""
    for entry in entries:
        # Prefer LLM-written summary/features when explicitly enabled; otherwise
        # (and on any failure) fall back to the heuristic extractors. For features,
        # an empty LLM result is intentional (no demoable surface) and is kept;
        # only None (disabled/failed) falls back.
        entry.detailed_summary = llm_summary(entry) or extract_detailed_summary(entry)
        _llm_feats = llm_key_features(entry.title, entry.content_html, entry.detailed_summary or entry.summary or "")
        entry.key_features = _llm_feats if _llm_feats is not None else extract_key_features(entry)

        # A human-verified override wins over any automated resolution: it's the
        # proven-correct page (or an intentional "no link" when no docs page fits).
        override = docs_override(entry.url)
        if override is not _NO_OVERRIDE:
            entry.docs_url = override  # a verified URL, or None to show no link
            print(f"  ✅ Verified docs override: {override or '(no docs link)'}")
            continue

        # Otherwise resolve the most accurate documentation URL. Only sets docs_url
        # if the page is verified as genuinely relevant (else the template omits it).
        print(f"  🔍 Searching docs for: {entry.title[:50]}...")
        entry.docs_url = search_docs_for_release(
            entry.title, entry.content_html, entry.detailed_summary or entry.summary or ""
        )

    return entries


def _fit_labels(labels: list[str], max_px: float = 240.0, max_count: int = 3) -> list[str]:
    """Keep the leading labels that fit a single pill row on the narrowest phones.

    Pills must always render as one horizontal row — never stacked — so emit
    labels only while their estimated rendered width fits ~240px (the card
    content width at a 320px viewport, with slack). Estimate: ~5.8px/char at
    11px Mona Sans/Helvetica, plus 22px pill chrome (10px*2 padding + 1px*2
    border) and a 6px gap between pills.
    """
    out: list[str] = []
    used = 0.0
    for label in labels[:max_count]:
        width = 5.8 * len(label) + 22 + (6 if out else 0)
        if used + width > max_px:
            break
        out.append(label)
        used += width
    return out


def _safe_href(url: Optional[str]) -> str:
    """Allow only http(s) URLs into email hrefs.

    The entry URL comes straight from the feed's <link>, which is
    attacker-influenceable in principle. Jinja2 autoescape stops an attacker
    breaking out of the href attribute, but it does NOT block a javascript: or
    data: scheme, so gate the scheme here at the data boundary.
    """
    if not url:
        return ""
    return url if urlsplit(url).scheme.lower() in ("http", "https") else ""


def entries_to_dict(entries: list[ChangelogEntry]) -> list[dict]:
    """Convert entries to the dicts the email template consumes."""
    result = []
    for e in entries:
        # Prefer the cleaned detailed_summary; fall back to the RSS summary.
        if e.detailed_summary:
            summary_text = e.detailed_summary
        elif e.summary:
            summary_text = _clean_html_to_text(e.summary)
        else:
            summary_text = ""

        result.append({
            "title": e.title,
            "url": _safe_href(e.url),
            "published": e.published,
            "summary": summary_text,
            "category": e.category,
            "labels": _fit_labels(e.labels),
            "docs_url": _safe_href(e.docs_url),
            "key_features": e.key_features,
        })
    return result
