"""
Changelog fetching, parsing, categorization, and demo outline extraction.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Optional
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import feedparser
import requests
from bs4 import BeautifulSoup

CHANGELOG_FEED_URL = "https://github.blog/changelog/feed/"
DOCS_BASE_URL = "https://docs.github.com"

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
        # Format nicely: "Jan 15, 2026 at 1:57 PM PT"
        return dt_pst.strftime("%b %d, %Y at %-I:%M %p PT")
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
    demo_outline: Optional[str] = None
    navigation_path: Optional[str] = None
    docs_url: Optional[str] = None


def fetch_changelog(max_age_days: int = MAX_AGE_DAYS) -> list[ChangelogEntry]:
    """Fetch and parse the GitHub changelog RSS feed.
    
    Only returns entries from the past max_age_days (default 7 days).
    """
    feed = feedparser.parse(CHANGELOG_FEED_URL)
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
        if entry.category == "Release":
            categorized["releases"].append(entry)
        elif entry.category == "Retired":
            categorized["retirements"].append(entry)
        else:
            categorized["improvements"].append(entry)

    return categorized


def search_github_docs(query: str) -> Optional[str]:
    """
    Search GitHub documentation for the most relevant page.
    Uses the GitHub docs search API to find accurate documentation.
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
        
        # Use GitHub's docs search (via their REST endpoint)
        search_url = f"https://docs.github.com/search?query={requests.utils.quote(search_query)}"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        }
        
        response = requests.get(search_url, headers=headers, timeout=10)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, "html.parser")
        
        # Look for search result links
        # GitHub docs search results are in specific elements
        for link in soup.find_all("a", href=True):
            href = link["href"]
            # Look for documentation article links (not search/navigation links)
            if href.startswith("/en/") and not any(x in href for x in ["/search", "/site-policy", "/get-started/learning-about-github"]):
                full_url = f"https://docs.github.com{href}"
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
        # - Embedded links (strict=False): 40% body OR 30% page title
        #   These come from GitHub's own changelog HTML, so moderate confidence is fine.
        # - Search results (strict=True): 60% body OR 50% page title
        #   Search results are often tangential; require strong evidence of relevance.
        if strict:
            body_threshold = 0.60
            title_threshold = 0.50
        else:
            body_threshold = 0.40
            title_threshold = 0.30

        if match_ratio >= body_threshold or title_ratio >= title_threshold:
            return True

        return False

    except Exception as e:
        print(f"  ⚠️  Could not validate docs URL '{url}': {e}")
        return False


def search_docs_for_release(title: str, content_html: str = "", summary: str = "") -> Optional[str]:
    """
    Find the most accurate documentation URL for a release.
    Only returns a URL if it has been validated as genuinely relevant
    to the specific changelog entry.
    
    Priority order:
      1. docs.github.com link embedded in the changelog HTML (most trustworthy)
      2. Any other embedded link (blog post, feature page) from the changelog HTML
      3. GitHub docs search result (least trustworthy — uses strict validation)
    """
    embedded_url = extract_docs_url(content_html) if content_html else None

    # Strategy 1: Embedded docs.github.com link — placed by GitHub in the changelog.
    if embedded_url and "docs.github.com" in embedded_url:
        if validate_docs_url(embedded_url, title, summary, strict=False):
            return embedded_url
        else:
            print(f"  ⚠️  Embedded docs URL rejected (not relevant): {embedded_url}")

    # Strategy 2: Other embedded link (blog post, feature page) from changelog HTML.
    if embedded_url and "docs.github.com" not in embedded_url:
        if validate_docs_url(embedded_url, title, summary, strict=False):
            return embedded_url
        else:
            print(f"  ⚠️  Embedded link rejected (not relevant): {embedded_url}")

    # Strategy 3: Search GitHub docs — least trustworthy, requires strict validation.
    search_result = search_github_docs(title)
    if search_result:
        if validate_docs_url(search_result, title, summary, strict=True):
            return search_result
        else:
            print(f"  ⚠️  Search docs URL rejected (not relevant enough): {search_result}")

    # No verified documentation found — return None so the template
    # does NOT show a "View Documentation" link.
    print(f"  ❌ No verified docs URL found for: {title[:60]}")
    return None


def extract_docs_url(content_html: str) -> Optional[str]:
    """
    Extract the most relevant documentation URL from changelog content.
    Prioritizes official GitHub docs, then other relevant links.
    """
    soup = BeautifulSoup(content_html, "html.parser")
    
    docs_links = []
    learn_more_links = []
    github_blog_links = []
    other_github_links = []
    
    for link in soup.find_all("a", href=True):
        href = link["href"]
        link_text = link.get_text().lower().strip()
        
        # Skip empty or anchor-only links
        if not href or href.startswith("#"):
            continue
        
        # Prioritize docs.github.com links
        if "docs.github.com" in href:
            docs_links.append(href)
        # "Learn more" or "documentation" links are usually the main docs
        elif any(phrase in link_text for phrase in ["learn more", "documentation", "read more", "see the docs", "view docs"]):
            learn_more_links.append(href)
        # GitHub blog posts about the feature
        elif "github.blog" in href and "/changelog/" not in href:
            github_blog_links.append(href)
        # Other github.com links (could be feature pages, help, etc.)
        elif "github.com" in href and "/changelog/" not in href:
            other_github_links.append(href)
    
    # Return in priority order
    if docs_links:
        return docs_links[0]
    if learn_more_links:
        return learn_more_links[0]
    if github_blog_links:
        return github_blog_links[0]
    if other_github_links:
        return other_github_links[0]
    
    return None


def extract_all_relevant_links(content_html: str) -> dict:
    """
    Extract all relevant links from changelog content for SE reference.
    Returns a dict with categorized links.
    """
    soup = BeautifulSoup(content_html, "html.parser")
    
    links = {
        "docs": None,
        "blog": None,
        "feature_page": None,
    }
    
    for link in soup.find_all("a", href=True):
        href = link["href"]
        link_text = link.get_text().lower().strip()
        
        if not href or href.startswith("#"):
            continue
        
        # Official documentation
        if "docs.github.com" in href and not links["docs"]:
            links["docs"] = href
        # Blog posts (feature announcements often have more detail)
        elif "github.blog" in href and "/changelog/" not in href and not links["blog"]:
            links["blog"] = href
        # Feature pages on github.com (like github.com/features/copilot)
        elif "github.com/features" in href and not links["feature_page"]:
            links["feature_page"] = href
    
    return links


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


def extract_detailed_summary(entry: ChangelogEntry) -> str:
    """
    Extract a concise, SE-focused summary from the changelog entry.
    Focuses on what the feature does and its value proposition.
    """
    # Start with content_html if available
    if entry.content_html:
        soup = BeautifulSoup(entry.content_html, "html.parser")
        
        # Remove script and style elements
        for element in soup(["script", "style"]):
            element.decompose()
        
        # Get text content
        text = soup.get_text(separator=" ", strip=True)
        
        # Remove common boilerplate
        boilerplate_patterns = [
            r"The post .+ appeared first on The GitHub Blog\.",
            r"The post .+ appeared first on GitHub Blog\.",
            r"appeared first on The GitHub Blog\.",
            r"appeared first on GitHub Blog\.",
            r"Learn more\s*$",
        ]
        for pattern in boilerplate_patterns:
            text = re.sub(pattern, "", text, flags=re.IGNORECASE).strip()
        
        # Keep it concise for SE use - focus on first 2-3 key sentences (~350 chars)
        sentences = re.split(r'(?<=[.!?])\s+', text)
        summary_sentences = []
        char_count = 0
        for sentence in sentences:
            # Skip sentences that are just links or very short
            if len(sentence) < 15:
                continue
            if char_count + len(sentence) <= 350:
                summary_sentences.append(sentence)
                char_count += len(sentence)
            else:
                break
        
        if summary_sentences:
            return " ".join(summary_sentences)
    
    # Fall back to RSS summary
    if entry.summary:
        soup = BeautifulSoup(entry.summary, "html.parser")
        text = soup.get_text().strip()
        # Remove boilerplate
        text = re.sub(r"The post .+ appeared first on The GitHub Blog\.", "", text, flags=re.IGNORECASE).strip()
        return text
    
    return ""


def extract_key_features(entry: ChangelogEntry) -> list[str]:
    """
    Extract key features relevant to SE demos.
    Focuses on capabilities, not implementation details.
    """
    features = []
    
    if entry.content_html:
        soup = BeautifulSoup(entry.content_html, "html.parser")
        
        # Look for list items (common in changelogs) - these are usually the key capabilities
        for li in soup.find_all("li"):
            text = li.get_text().strip()
            if 15 < len(text) < 150:  # Concise feature descriptions
                # Clean up the text
                text = re.sub(r'\s+', ' ', text)
                features.append(text)
        
        # Also look for bold/strong text as key points
        for strong in soup.find_all(["strong", "b"]):
            text = strong.get_text().strip()
            if 5 < len(text) < 80 and text not in features:
                features.append(text)
    
    return features[:4]  # Keep it focused - top 4 key features for demos


def infer_feature_context(entry: ChangelogEntry) -> dict:
    """
    Build a click-by-click demo skeleton with conversational talking points.
    Uses actual content from the entry to generate specific, relevant demos.
    """
    title_lower = entry.title.lower()
    labels_lower = " ".join(entry.labels).lower()
    content_lower = entry.content_html.lower() if entry.content_html else ""
    summary_lower = entry.summary.lower() if entry.summary else ""
    all_text = f"{title_lower} {labels_lower} {content_lower} {summary_lower}"
    
    # Extract key information from the entry
    detailed_summary = extract_detailed_summary(entry)
    key_features = extract_key_features(entry)
    feature_title = entry.title
    
    # Each step is a tuple: (click/action, what to say naturally)
    context = {
        "area": "general",
        "navigation": None,
        "demo_flow": [],  # List of {"click": "...", "say": "..."}
        "detailed_summary": detailed_summary,
        "key_features": key_features,
    }
    
    # Build feature-specific talking points from actual content
    feature_highlight = detailed_summary[:200] + "..." if len(detailed_summary) > 200 else detailed_summary
    
    # Build specific benefit statements from key features
    feature_benefits = ""
    if key_features:
        feature_benefits = f"Key capabilities include: {key_features[0]}"
        if len(key_features) > 1:
            feature_benefits += f", and {key_features[1]}"
    
    if "copilot" in all_text:
        context["area"] = "copilot"
        if "agent" in all_text or "agentic" in all_text:
            context["navigation"] = "VS Code → Copilot Chat → Agent Mode"
            context["demo_flow"] = [
                {
                    "click": "Open VS Code with a project that has multiple files",
                    "say": f"{feature_title} — {feature_highlight}"
                },
                {
                    "click": "Press Cmd+Shift+I to open Copilot Chat, then enable 'Agent' mode",
                    "say": f"Agent mode is the key here. {feature_benefits}" if feature_benefits else "Agent mode lets Copilot work autonomously across your entire codebase."
                },
                {
                    "click": "Type a multi-step request like 'Add error handling to all API calls'",
                    "say": "Watch it analyze the codebase, identify all the relevant files, and propose changes — this would take a developer 30+ minutes manually."
                },
                {
                    "click": "Review the diff and click 'Accept' on changes you approve",
                    "say": "You're always in control. Nothing changes until you review and accept. That's the key for enterprise adoption."
                },
            ]
        elif "chat" in all_text:
            context["navigation"] = "VS Code → Copilot Chat (Cmd+Shift+I)"
            context["demo_flow"] = [
                {
                    "click": "Open VS Code and navigate to a complex file",
                    "say": f"{feature_title} — {feature_highlight}"
                },
                {
                    "click": "Select a block of code, right-click → 'Copilot' → 'Explain This'",
                    "say": f"{feature_benefits}" if feature_benefits else "Instead of searching docs or asking the original author, just ask Copilot."
                },
                {
                    "click": "In the Chat panel, type '@workspace how does authentication work?'",
                    "say": "The @workspace command searches your entire project — it understands context across all files, not just the one you're looking at."
                },
            ]
        elif "code review" in all_text or "review" in all_text:
            context["navigation"] = "github.com → Pull Request → Files changed tab"
            context["demo_flow"] = [
                {
                    "click": "Open a PR with 10+ file changes",
                    "say": f"{feature_title} — {feature_highlight}"
                },
                {
                    "click": "Click the Copilot icon in the PR toolbar → 'Summarize' or 'Review'",
                    "say": f"{feature_benefits}" if feature_benefits else "One click for AI-powered code review that catches security issues, bugs, and performance problems."
                },
                {
                    "click": "Scroll through the suggestions — click 'Apply' on any you want to accept",
                    "say": "Developers don't have to figure out the fix themselves. Copilot proposes the code change and they just approve it."
                },
            ]
        elif "extension" in all_text or "extensions" in all_text:
            context["navigation"] = "github.com → Marketplace → Copilot Extensions"
            context["demo_flow"] = [
                {
                    "click": "Go to github.com/marketplace and filter by 'Copilot Extensions'",
                    "say": f"{feature_title} — {feature_highlight}"
                },
                {
                    "click": "Browse available extensions or search for a specific tool integration",
                    "say": f"{feature_benefits}" if feature_benefits else "Extensions let third-party tools integrate directly into Copilot Chat — so developers can query Datadog, Sentry, or your internal tools without leaving their editor."
                },
                {
                    "click": "Install an extension and demo using it via @extension-name in Copilot Chat",
                    "say": "This is how you extend Copilot's capabilities for your specific tech stack and internal tools."
                },
            ]
        else:
            context["navigation"] = "github.com → Settings → Copilot"
            context["demo_flow"] = [
                {
                    "click": "Navigate to your organization or user settings → Copilot",
                    "say": f"{feature_title} — {feature_highlight}"
                },
                {
                    "click": "Show the relevant configuration or feature toggle",
                    "say": f"{feature_benefits}" if feature_benefits else "Here's where admins control Copilot policies and features for their organization."
                },
                {
                    "click": "Demo the feature in VS Code or github.com",
                    "say": "Let me show you what this looks like from a developer's perspective."
                },
            ]
    
    elif "actions" in all_text or "workflow" in all_text:
        context["area"] = "actions"
        if "runner" in all_text or "runners" in all_text:
            context["navigation"] = "Repository → Settings → Actions → Runners"
            context["demo_flow"] = [
                {
                    "click": "Go to any repository → Settings → Actions → Runners",
                    "say": f"{feature_title} — {feature_highlight}"
                },
                {
                    "click": "Click 'New self-hosted runner' or view existing runner configuration",
                    "say": f"{feature_benefits}" if feature_benefits else "You can use GitHub-hosted runners (we manage everything) or self-hosted for compliance and specialized hardware."
                },
                {
                    "click": "Show the runner logs or trigger a workflow to demonstrate",
                    "say": "This is where you get visibility into build times, resource usage, and troubleshooting."
                },
            ]
        elif "reusable" in all_text or "composite" in all_text:
            context["navigation"] = "Repository → .github/workflows/ → workflow file"
            context["demo_flow"] = [
                {
                    "click": "Open a workflow YAML file that uses 'uses: ./.github/actions/' or 'workflow_call'",
                    "say": f"{feature_title} — {feature_highlight}"
                },
                {
                    "click": "Show the reusable workflow or composite action definition",
                    "say": f"{feature_benefits}" if feature_benefits else "Reusable workflows let you define CI/CD logic once and call it from multiple repos — DRY principle for DevOps."
                },
                {
                    "click": "Trigger the workflow and show the execution graph",
                    "say": "The Actions UI shows exactly which reusable components ran and their individual status."
                },
            ]
        else:
            context["navigation"] = "Repository → Actions tab"
            context["demo_flow"] = [
                {
                    "click": "Click the 'Actions' tab in any repository",
                    "say": f"{feature_title} — {feature_highlight}"
                },
                {
                    "click": "Click into a recent workflow run to show the execution details",
                    "say": f"{feature_benefits}" if feature_benefits else "Actions gives you native CI/CD without managing separate infrastructure — one less tool in your stack."
                },
                {
                    "click": "Show the workflow YAML and how it maps to the visual execution",
                    "say": "Everything is code. Version controlled, reviewable, and auditable."
                },
            ]
    
    elif "security" in all_text or "dependabot" in all_text or "secret" in all_text or "vulnerability" in all_text or "ghas" in all_text or "codeql" in all_text:
        context["area"] = "security"
        if "dependabot" in all_text:
            context["navigation"] = "Repository → Security tab → Dependabot"
            context["demo_flow"] = [
                {
                    "click": "Go to any repository → Security tab → Dependabot alerts",
                    "say": f"{feature_title} — {feature_highlight}"
                },
                {
                    "click": "Click into a vulnerability alert to show the details and remediation",
                    "say": f"{feature_benefits}" if feature_benefits else "Dependabot continuously scans your dependency tree and alerts you to known vulnerabilities."
                },
                {
                    "click": "Show a Dependabot PR that auto-updates a vulnerable package",
                    "say": "It doesn't just report problems — it opens PRs with fixes. Your team just reviews and merges."
                },
            ]
        elif "secret" in all_text:
            context["navigation"] = "Repository → Settings → Code security → Secret scanning"
            context["demo_flow"] = [
                {
                    "click": "Go to Settings → Code security and analysis → Secret scanning",
                    "say": f"{feature_title} — {feature_highlight}"
                },
                {
                    "click": "Show the Push protection toggle and explain what it does",
                    "say": f"{feature_benefits}" if feature_benefits else "Push protection blocks secrets BEFORE they're committed — not after the damage is done."
                },
                {
                    "click": "Go to Security tab → Secret scanning alerts to show detected secrets",
                    "say": "Full audit trail of what was found, when, and remediation status. This is what your security team needs."
                },
            ]
        elif "code scanning" in all_text or "codeql" in all_text:
            context["navigation"] = "Repository → Security tab → Code scanning"
            context["demo_flow"] = [
                {
                    "click": "Go to Security tab → Code scanning alerts",
                    "say": f"{feature_title} — {feature_highlight}"
                },
                {
                    "click": "Click into a finding to show the data flow visualization",
                    "say": f"{feature_benefits}" if feature_benefits else "CodeQL does semantic analysis — it traces how untrusted input flows to sensitive operations. This catches real vulnerabilities, not just pattern matches."
                },
                {
                    "click": "Show a PR where code scanning blocked a vulnerability from merging",
                    "say": "This runs on every PR automatically. Vulnerabilities get caught before they hit main branch."
                },
            ]
        else:
            context["navigation"] = "Repository → Security tab → Overview"
            context["demo_flow"] = [
                {
                    "click": "Click the Security tab → Security overview",
                    "say": f"{feature_title} — {feature_highlight}"
                },
                {
                    "click": "Walk through the different security features shown",
                    "say": f"{feature_benefits}" if feature_benefits else "This is your security command center — one place to see Dependabot, secret scanning, and code scanning status."
                },
                {
                    "click": "Click into specific alerts to show remediation workflows",
                    "say": "Security teams get visibility, developers get actionable fixes. That's the balance you need."
                },
            ]
    
    elif "issues" in all_text or "projects" in all_text or "project" in all_text:
        context["area"] = "projects"
        context["navigation"] = "Repository or Organization → Projects tab"
        context["demo_flow"] = [
            {
                "click": "Go to a repository or org → Projects tab → Open a project board",
                "say": f"{feature_title} — {feature_highlight}"
            },
            {
                "click": "Show the different views: Board, Table, Roadmap",
                "say": f"{feature_benefits}" if feature_benefits else "GitHub Projects is planning that lives where your code lives — no context switching to Jira or Monday."
            },
            {
                "click": "Demo adding an item, changing status, or using automation",
                "say": "Issues and PRs automatically flow through the board based on their state. Less manual status updates."
            },
        ]
    
    elif "pull request" in all_text or "merge" in all_text or " pr " in all_text:
        context["area"] = "pull_requests"
        context["navigation"] = "Repository → Pull requests tab"
        context["demo_flow"] = [
            {
                "click": "Go to Pull requests tab and open a PR with active discussion",
                "say": f"{feature_title} — {feature_highlight}"
            },
            {
                "click": "Show the specific feature in the PR interface",
                "say": f"{feature_benefits}" if feature_benefits else "Pull requests are the heart of code collaboration on GitHub."
            },
            {
                "click": "Walk through how this improves the review or merge workflow",
                "say": "This directly addresses pain points teams have with code review velocity and quality."
            },
        ]
    
    elif "codespace" in all_text:
        context["area"] = "codespaces"
        context["navigation"] = "Repository → Code button → Codespaces tab"
        context["demo_flow"] = [
            {
                "click": "Go to any repository → Click green 'Code' button → Codespaces tab",
                "say": f"{feature_title} — {feature_highlight}"
            },
            {
                "click": "Click 'Create codespace on main' and wait for it to spin up",
                "say": f"{feature_benefits}" if feature_benefits else "Full dev environment in the cloud — VS Code, terminal, extensions, everything. Compare this to 'works on my machine' problems."
            },
            {
                "click": "Show the devcontainer.json that defines the environment",
                "say": "This is infrastructure as code for dev environments. Every developer gets an identical setup."
            },
        ]
    
    elif "api" in all_text or "graphql" in all_text or "rest" in all_text:
        context["area"] = "api"
        context["navigation"] = "docs.github.com/rest or GraphQL Explorer"
        context["demo_flow"] = [
            {
                "click": "Open docs.github.com/rest and search for the relevant endpoint",
                "say": f"{feature_title} — {feature_highlight}"
            },
            {
                "click": "Show the endpoint documentation with request/response examples",
                "say": f"{feature_benefits}" if feature_benefits else "GitHub is API-first — anything you can do in the UI, you can automate via API."
            },
            {
                "click": "Demo a curl command or show the GitHub CLI equivalent",
                "say": "Quick demo: here's what the response looks like. Everything you need for custom integrations."
            },
        ]
    
    else:
        # Default: create demo based on extracted content with specific feature info
        context["navigation"] = "github.com"
        context["demo_flow"] = [
            {
                "click": "Navigate to the feature area in GitHub",
                "say": f"{feature_title} — {feature_highlight}"
            },
            {
                "click": "Locate and demonstrate the new capability",
                "say": f"{feature_benefits}" if feature_benefits else "Here's the key value: this addresses a common pain point teams face."
            },
            {
                "click": "Show a real-world example of how this helps developers",
                "say": "This is how it fits into day-to-day workflows — less friction, faster delivery."
            },
        ]
    
    return context


def generate_demo_outline(entry: ChangelogEntry) -> str:
    """Generate a click-by-click demo skeleton with conversational talking points."""
    context = infer_feature_context(entry)
    
    # Store enhanced data on the entry for template use
    entry.demo_context = context
    
    outline_parts = []
    outline_parts.append(f"## {entry.title}")
    outline_parts.append("")
    
    # Navigation
    if entry.navigation_path:
        outline_parts.append(f"**Start:** `{entry.navigation_path}`")
    elif context["navigation"]:
        outline_parts.append(f"**Start:** `{context['navigation']}`")
        entry.navigation_path = context["navigation"]
    outline_parts.append("")
    
    # Demo flow - click by click with talking points
    outline_parts.append("**Demo Flow:**")
    for i, step in enumerate(context["demo_flow"], 1):
        outline_parts.append(f"{i}. **{step['click']}**")
        outline_parts.append(f"   _\"{step['say']}\"_")
    outline_parts.append("")
    
    # Links
    if entry.docs_url:
        outline_parts.append(f"**Documentation:** [View full docs]({entry.docs_url})")
    outline_parts.append(f"**Changelog:** [Read more]({entry.url})")
    
    return "\n".join(outline_parts)


def enrich_entries_with_demo_outlines(entries: list[ChangelogEntry]) -> list[ChangelogEntry]:
    """Add demo outlines and detailed summaries to all entries."""
    for entry in entries:
        # Extract detailed summary for ALL entries (not just releases)
        entry.detailed_summary = extract_detailed_summary(entry)
        entry.key_features = extract_key_features(entry)
        
        # Search for the most accurate documentation URL
        # Only sets docs_url if the page is verified as genuinely relevant
        print(f"  🔍 Searching docs for: {entry.title[:50]}...")
        entry.docs_url = search_docs_for_release(entry.title, entry.content_html, entry.summary or "")
        
        # Also extract all relevant links for reference
        entry.all_links = extract_all_relevant_links(entry.content_html)
        
        if entry.category == "Release":
            # Try to extract navigation path from docs
            if entry.docs_url:
                entry.navigation_path = extract_navigation_path(entry.docs_url)

            # Generate demo outline
            entry.demo_outline = generate_demo_outline(entry)
        else:
            # For improvements and retirements, still add context
            entry.demo_context = infer_feature_context(entry)

    return entries


def entries_to_dict(entries: list[ChangelogEntry]) -> list[dict]:
    """Convert entries to dictionaries for JSON serialization and templating."""
    result = []
    for e in entries:
        # Clean up summary - remove the "appeared first on The GitHub Blog" boilerplate
        summary_text = ""
        if e.summary:
            summary_text = BeautifulSoup(e.summary, "html.parser").get_text().strip()
            # Remove common RSS boilerplate patterns
            boilerplate_patterns = [
                r"The post .+ appeared first on The GitHub Blog\.",
                r"The post .+ appeared first on GitHub Blog\.",
                r"appeared first on The GitHub Blog\.",
                r"appeared first on GitHub Blog\.",
            ]
            import re
            for pattern in boilerplate_patterns:
                summary_text = re.sub(pattern, "", summary_text, flags=re.IGNORECASE).strip()
        
        # Use detailed_summary if available and summary is empty or short
        detailed_summary = getattr(e, 'detailed_summary', '')
        if detailed_summary and (not summary_text or len(summary_text) < 50):
            summary_text = detailed_summary
        elif detailed_summary and len(detailed_summary) > len(summary_text):
            summary_text = detailed_summary
        
        result.append({
            "title": e.title,
            "url": e.url,
            "published": e.published,
            "summary": summary_text,
            "content_html": e.content_html,
            "category": e.category,
            "labels": e.labels,
            "demo_outline": e.demo_outline,
            "navigation_path": e.navigation_path,
            "docs_url": e.docs_url,
            "demo_context": getattr(e, 'demo_context', None),
            "detailed_summary": detailed_summary,
            "key_features": getattr(e, 'key_features', []),
            "all_links": getattr(e, 'all_links', {}),
        })
    return result
