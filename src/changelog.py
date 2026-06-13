"""
Changelog fetching, parsing, categorization, and docs-link resolution.
"""

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
        response = requests.head(url, headers=headers, timeout=10, allow_redirects=True)
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
        
        response = requests.get(search_url, headers=headers, timeout=10)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, "html.parser")
        
        # Look for search result links — only accept enterprise docs
        for link in soup.find_all("a", href=True):
            href = link["href"]
            skip = ["/search", "/site-policy", "/get-started/learning-about-github"]
            if href.startswith("/en/") and not any(x in href for x in skip):
                full_url = f"https://docs.github.com{href}"
                if not prefer_enterprise:
                    return full_url
                if is_enterprise_docs_url(full_url):
                    return full_url
        
        return None
        
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
        response = requests.get(url, headers=headers, timeout=10, allow_redirects=True)
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
    best = None
    best_score = -1.0
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if not href or href.startswith("#") or "docs.github.com" not in href:
            continue
        path = href.lower()
        score = float(sum(1 for kw in keywords if kw in path))
        if is_enterprise_docs_url(href):
            score += 0.5  # tie-break toward enterprise docs
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


def extract_detailed_summary(entry: ChangelogEntry) -> str:
    """
    Extract a concise, well-formed summary from the changelog entry.
    Skips filler/preamble sentences and focuses on what the feature does.
    """
    if entry.content_html:
        text = _clean_html_to_text(entry.content_html)

        # Split into sentences and select meaningful ones
        sentences = re.split(r'(?<=[.!?])\s+', text)
        summary_sentences = []
        char_count = 0
        for sentence in sentences:
            sentence = sentence.strip()
            # Skip very short fragments
            if len(sentence) < 15:
                continue
            # Skip filler/preamble
            if _is_filler_sentence(sentence):
                continue
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
    # Capitalize the first letter (skip if starts with a code token like .NET or a lowercase package name)
    if text and text[0].islower() and not text.startswith(('.', '@')):
        text = text[0].upper() + text[1:]
    # Bullet points never need trailing periods — keep them consistent
    text = text.rstrip('.')
    return text


def extract_key_features(entry: ChangelogEntry) -> list[str]:
    """
    Extract key features relevant to SE demos.
    Focuses on capabilities, not implementation details.
    Returns up to 4 clean, deduplicated bullet points.
    """
    features = []
    title_lower = entry.title.lower()

    if entry.content_html:
        soup = BeautifulSoup(entry.content_html, "html.parser")

        # Look for list items (common in changelogs)
        for li in soup.find_all("li"):
            text = li.get_text().strip()
            if 15 < len(text) < 150:
                text = _normalize_feature_text(text)
                if text:
                    features.append(text)

        # Also look for bold/strong text as key points
        for strong in soup.find_all(["strong", "b"]):
            text = strong.get_text().strip()
            if 5 < len(text) < 80:
                text = _normalize_feature_text(text)
                if text and text not in features:
                    features.append(text)

    # Deduplicate: remove features that are substrings of the title or of each other
    deduped = []
    for f in features:
        f_lower = f.lower()
        # Skip if it's essentially the title restated
        if f_lower in title_lower or title_lower in f_lower:
            continue
        # Skip if it's a substring of an already-kept feature
        if any(f_lower in kept.lower() or kept.lower() in f_lower for kept in deduped):
            continue
        deduped.append(f)

    return deduped[:4]


def enrich_entries(entries: list[ChangelogEntry]) -> list[ChangelogEntry]:
    """Attach a cleaned summary, key features, and a verified docs link to each entry."""
    for entry in entries:
        entry.detailed_summary = extract_detailed_summary(entry)
        entry.key_features = extract_key_features(entry)

        # Search for the most accurate documentation URL. Only sets docs_url if
        # the page is verified as genuinely relevant (else the template omits it).
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
    11px Mona Sans/Helvetica, plus 20px pill chrome (padding + border) and a
    6px gap between pills.
    """
    out: list[str] = []
    used = 0.0
    for label in labels[:max_count]:
        width = 5.8 * len(label) + 20 + (6 if out else 0)
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
