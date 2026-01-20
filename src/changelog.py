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
        
        # Alternative: Try direct URL construction for common patterns
        return construct_docs_url_from_keywords(keywords)
        
    except Exception as e:
        print(f"  ⚠️  Docs search failed for '{query[:50]}...': {e}")
        return None


def construct_docs_url_from_keywords(keywords: list[str]) -> Optional[str]:
    """
    Construct a likely docs URL based on keywords.
    Maps common GitHub features to their documentation paths.
    """
    keywords_lower = [k.lower() for k in keywords]
    keywords_set = set(keywords_lower)
    
    # Map of feature keywords to documentation paths
    docs_mapping = {
        # Copilot
        frozenset(['copilot']): 'https://docs.github.com/en/copilot',
        frozenset(['copilot', 'chat']): 'https://docs.github.com/en/copilot/using-github-copilot/asking-github-copilot-questions-in-your-ide',
        frozenset(['copilot', 'agent']): 'https://docs.github.com/en/copilot/using-github-copilot/using-copilot-coding-agent-to-work-on-tasks',
        frozenset(['copilot', 'review']): 'https://docs.github.com/en/copilot/using-github-copilot/code-review/using-copilot-code-review',
        frozenset(['copilot', 'extensions']): 'https://docs.github.com/en/copilot/using-github-copilot/using-extensions-to-integrate-external-tools-with-copilot-chat',
        
        # Actions
        frozenset(['actions']): 'https://docs.github.com/en/actions',
        frozenset(['actions', 'runner']): 'https://docs.github.com/en/actions/using-github-hosted-runners',
        frozenset(['actions', 'workflow']): 'https://docs.github.com/en/actions/writing-workflows',
        frozenset(['actions', 'cache']): 'https://docs.github.com/en/actions/using-workflows/caching-dependencies-to-speed-up-workflows',
        
        # Security
        frozenset(['security']): 'https://docs.github.com/en/code-security',
        frozenset(['dependabot']): 'https://docs.github.com/en/code-security/dependabot',
        frozenset(['secret', 'scanning']): 'https://docs.github.com/en/code-security/secret-scanning',
        frozenset(['code', 'scanning']): 'https://docs.github.com/en/code-security/code-scanning',
        frozenset(['codeql']): 'https://docs.github.com/en/code-security/code-scanning/introduction-to-code-scanning/about-code-scanning-with-codeql',
        frozenset(['security', 'advisories']): 'https://docs.github.com/en/code-security/security-advisories',
        
        # Codespaces
        frozenset(['codespaces']): 'https://docs.github.com/en/codespaces',
        frozenset(['codespace']): 'https://docs.github.com/en/codespaces',
        
        # Projects & Issues
        frozenset(['projects']): 'https://docs.github.com/en/issues/planning-and-tracking-with-projects',
        frozenset(['issues']): 'https://docs.github.com/en/issues',
        
        # Pull Requests
        frozenset(['pull', 'request']): 'https://docs.github.com/en/pull-requests',
        frozenset(['merge']): 'https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/incorporating-changes-from-a-pull-request',
        
        # Repositories
        frozenset(['repository']): 'https://docs.github.com/en/repositories',
        frozenset(['branch', 'protection']): 'https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches',
        frozenset(['rulesets']): 'https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets',
        
        # API
        frozenset(['api']): 'https://docs.github.com/en/rest',
        frozenset(['graphql']): 'https://docs.github.com/en/graphql',
        frozenset(['rest', 'api']): 'https://docs.github.com/en/rest',
        
        # Enterprise
        frozenset(['enterprise']): 'https://docs.github.com/en/enterprise-cloud@latest',
        frozenset(['audit', 'log']): 'https://docs.github.com/en/enterprise-cloud@latest/admin/monitoring-activity-in-your-enterprise/reviewing-audit-logs-for-your-enterprise',
        
        # Packages
        frozenset(['packages']): 'https://docs.github.com/en/packages',
        frozenset(['container', 'registry']): 'https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry',
        
        # Mobile
        frozenset(['mobile']): 'https://docs.github.com/en/get-started/using-github/github-mobile',
        
        # CLI
        frozenset(['cli']): 'https://docs.github.com/en/github-cli',
        frozenset(['gh']): 'https://docs.github.com/en/github-cli',
    }
    
    # Find best matching docs URL
    best_match = None
    best_match_score = 0
    
    for feature_keywords, url in docs_mapping.items():
        # Count how many feature keywords match
        match_score = len(feature_keywords & keywords_set)
        if match_score > best_match_score:
            best_match_score = match_score
            best_match = url
    
    return best_match


def search_docs_for_release(title: str, content_html: str = "") -> Optional[str]:
    """
    Find the most accurate documentation URL for a release.
    Combines multiple search strategies for best results.
    """
    # Strategy 1: First check if docs URL is embedded in the content
    embedded_url = extract_docs_url(content_html) if content_html else None
    if embedded_url and "docs.github.com" in embedded_url:
        return embedded_url
    
    # Strategy 2: Search GitHub docs using the title
    search_result = search_github_docs(title)
    if search_result:
        return search_result
    
    # Strategy 3: Return embedded non-docs URL if available
    if embedded_url:
        return embedded_url
    
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
    
    # Build feature-specific talking points
    feature_highlight = detailed_summary[:150] + "..." if len(detailed_summary) > 150 else detailed_summary
    
    if "copilot" in all_text:
        context["area"] = "copilot"
        if "agent" in all_text or "agentic" in all_text:
            context["navigation"] = "VS Code → Copilot Chat → Agent Mode"
            context["demo_flow"] = [
                {
                    "click": "Open VS Code with a real project",
                    "say": f"Let me show you {feature_title}. {feature_highlight}"
                },
                {
                    "click": "Open Copilot Chat with Cmd+Shift+I",
                    "say": "This is Copilot Chat — your AI pair programmer that understands your entire codebase."
                },
                {
                    "click": "Toggle on 'Agent' mode at the top",
                    "say": "Agent mode lets Copilot work across multiple files autonomously — not just answer questions."
                },
                {
                    "click": "Give it a multi-step task relevant to the new feature",
                    "say": "Watch what happens — I'm giving it a real task that would normally take 30+ minutes."
                },
                {
                    "click": "Review the proposed changes",
                    "say": "You're still in control. Nothing changes until you review and accept. It's like having a junior dev who does the work, but you sign off."
                },
            ]
        elif "chat" in all_text:
            context["navigation"] = "VS Code → Copilot Chat"
            context["demo_flow"] = [
                {
                    "click": "Open a complex file in VS Code",
                    "say": f"Let me demo {feature_title}. {feature_highlight}"
                },
                {
                    "click": "Select code and open Copilot Chat",
                    "say": "Instead of hunting down the original author, just ask Copilot."
                },
                {
                    "click": "Ask about the selected code",
                    "say": "Plain English explanation. And it's not surface-level — it understands context."
                },
                {
                    "click": "Use @workspace to ask about the project",
                    "say": "Here's the real power — ask about the entire project, not just one file."
                },
            ]
        elif "code review" in all_text or "review" in all_text:
            context["navigation"] = "github.com → Pull Request → Files Changed"
            context["demo_flow"] = [
                {
                    "click": "Open a PR with substantial changes",
                    "say": f"This is {feature_title}. {feature_highlight}"
                },
                {
                    "click": "Click the Copilot icon → 'Review changes'",
                    "say": "One click to get AI-powered code review."
                },
                {
                    "click": "Review the summary and suggestions",
                    "say": "It catches security issues, performance problems, bugs — things that might slip through manual review."
                },
                {
                    "click": "Apply a suggestion with one click",
                    "say": "Developer doesn't have to figure out the fix — it's done for them."
                },
            ]
        else:
            context["navigation"] = "Settings → Copilot"
            context["demo_flow"] = [
                {
                    "click": "Navigate to Copilot settings",
                    "say": f"Let me show you {feature_title}. {feature_highlight}"
                },
                {
                    "click": "Walk through the new feature",
                    "say": "Here's the key capability and how it fits into developer workflow."
                },
                {
                    "click": "Demo it in action",
                    "say": "Let me show you what this looks like when a developer actually uses it."
                },
            ]
    
    elif "actions" in all_text or "workflow" in all_text:
        context["area"] = "actions"
        if "runner" in all_text:
            context["navigation"] = "Repository → Settings → Actions → Runners"
            context["demo_flow"] = [
                {
                    "click": "Go to Settings → Actions → Runners",
                    "say": f"This is {feature_title}. {feature_highlight}"
                },
                {
                    "click": "Show the runner configuration",
                    "say": "You've got hosted runners (we manage everything) or self-hosted for compliance/specialized hardware."
                },
                {
                    "click": "Configure the new capability",
                    "say": "Here's the new feature — this directly addresses common pain points."
                },
                {
                    "click": "Trigger a workflow to demonstrate",
                    "say": "Let me run something so you can see it in action."
                },
            ]
        else:
            context["navigation"] = "Repository → Actions"
            context["demo_flow"] = [
                {
                    "click": "Click the 'Actions' tab",
                    "say": f"This is {feature_title}. {feature_highlight}"
                },
                {
                    "click": "Show the workflow configuration",
                    "say": "Actions is GitHub's native CI/CD — one less tool to manage."
                },
                {
                    "click": "Demo the new capability",
                    "say": "Here's what this means for your team's workflow."
                },
                {
                    "click": "Show the results",
                    "say": "Full visibility, all in one place."
                },
            ]
    
    elif "security" in all_text or "dependabot" in all_text or "secret" in all_text or "vulnerability" in all_text or "ghas" in all_text or "codeql" in all_text:
        context["area"] = "security"
        if "dependabot" in all_text:
            context["navigation"] = "Repository → Security → Dependabot"
            context["demo_flow"] = [
                {
                    "click": "Go to Security tab → Dependabot alerts",
                    "say": f"This is {feature_title}. {feature_highlight}"
                },
                {
                    "click": "Show vulnerability alerts",
                    "say": "Dependabot scans dependencies continuously. When something's vulnerable, you know immediately."
                },
                {
                    "click": "Show the auto-generated fix PR",
                    "say": "It doesn't just report problems — it opens PRs with fixes. Your team just reviews and merges."
                },
                {
                    "click": "Show the dependency graph",
                    "say": "Full visibility into your dependency tree."
                },
            ]
        elif "secret" in all_text:
            context["navigation"] = "Repository → Settings → Code security → Secret scanning"
            context["demo_flow"] = [
                {
                    "click": "Go to Settings → Code security",
                    "say": f"This is {feature_title}. {feature_highlight}"
                },
                {
                    "click": "Show Secret scanning and Push protection",
                    "say": "Secret scanning finds leaked credentials. Push protection blocks them before they land."
                },
                {
                    "click": "Demo the detection/blocking",
                    "say": "Let me show you what happens when someone tries to commit a secret."
                },
                {
                    "click": "Show the alerts dashboard",
                    "say": "Full audit trail. Your security team has complete visibility."
                },
            ]
        elif "code scanning" in all_text or "codeql" in all_text:
            context["navigation"] = "Repository → Security → Code scanning"
            context["demo_flow"] = [
                {
                    "click": "Go to Security → Code scanning",
                    "say": f"This is {feature_title}. {feature_highlight}"
                },
                {
                    "click": "Show the alerts",
                    "say": "CodeQL does semantic analysis — it finds vulnerabilities that pattern matching misses."
                },
                {
                    "click": "Click into a finding",
                    "say": "Full data flow visualization. See exactly how user input reaches the vulnerable code."
                },
                {
                    "click": "Show PR integration",
                    "say": "This runs on every PR — vulnerabilities caught before they merge."
                },
            ]
        else:
            context["navigation"] = "Repository → Security"
            context["demo_flow"] = [
                {
                    "click": "Go to the Security tab",
                    "say": f"This is {feature_title}. {feature_highlight}"
                },
                {
                    "click": "Walk through the overview",
                    "say": "This is your security command center."
                },
                {
                    "click": "Demo the new capability",
                    "say": "Here's the new feature in action."
                },
            ]
    
    elif "issues" in all_text or "projects" in all_text or "project" in all_text:
        context["area"] = "projects"
        context["navigation"] = "Organization or Repository → Projects"
        context["demo_flow"] = [
            {
                "click": "Go to Projects tab",
                "say": f"This is {feature_title}. {feature_highlight}"
            },
            {
                "click": "Open or create a project",
                "say": "GitHub Projects is built-in planning that lives where your code lives."
            },
            {
                "click": "Demo the new capability",
                "say": "Here's the new feature and how it improves workflow."
            },
            {
                "click": "Show automation options",
                "say": "Automations keep things moving without manual work."
            },
        ]
    
    elif "pull request" in all_text or "merge" in all_text:
        context["area"] = "pull_requests"
        context["navigation"] = "Repository → Pull requests"
        context["demo_flow"] = [
            {
                "click": "Go to Pull requests tab",
                "say": f"This is {feature_title}. {feature_highlight}"
            },
            {
                "click": "Open or create a PR",
                "say": "Pull requests are the heart of code collaboration on GitHub."
            },
            {
                "click": "Demo the new feature",
                "say": "Here's what just shipped and how it helps your team."
            },
            {
                "click": "Show it in a review workflow",
                "say": "See how it fits naturally into the process."
            },
        ]
    
    elif "codespace" in all_text:
        context["area"] = "codespaces"
        context["navigation"] = "Repository → Code → Codespaces"
        context["demo_flow"] = [
            {
                "click": "Click the green 'Code' button",
                "say": f"This is {feature_title}. {feature_highlight}"
            },
            {
                "click": "Create a new Codespace",
                "say": "Full dev environment spinning up. Compare this to setting up a new laptop."
            },
            {
                "click": "Show VS Code in the browser",
                "say": "Real environment. Real terminal. Ready to code on any device."
            },
            {
                "click": "Demo the new capability",
                "say": "Here's the improvement and what it means for developers."
            },
        ]
    
    elif "api" in all_text or "graphql" in all_text or "rest" in all_text:
        context["area"] = "api"
        context["navigation"] = "docs.github.com/rest or GraphQL Explorer"
        context["demo_flow"] = [
            {
                "click": "Open the API documentation",
                "say": f"This is {feature_title}. {feature_highlight}"
            },
            {
                "click": "Find the relevant endpoint",
                "say": "GitHub is API-first. Anything in the UI can be automated."
            },
            {
                "click": "Demo with curl or gh CLI",
                "say": "Quick demo from the command line."
            },
            {
                "click": "Show the response",
                "say": "Everything you need to build automation or custom tooling."
            },
        ]
    
    else:
        # Default: create demo based on extracted content
        context["navigation"] = "GitHub.com"
        context["demo_flow"] = [
            {
                "click": "Navigate to the feature area",
                "say": f"Let me show you {feature_title}. {feature_highlight}"
            },
            {
                "click": "Locate the new capability",
                "say": "Here's where this lives in GitHub."
            },
            {
                "click": "Demo the feature",
                "say": "Let me show you what it looks like in practice."
            },
            {
                "click": "Highlight the key benefit",
                "say": "This is how it improves your team's workflow."
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
        # This uses web search + keyword mapping for best results
        print(f"  🔍 Searching docs for: {entry.title[:50]}...")
        entry.docs_url = search_docs_for_release(entry.title, entry.content_html)
        
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
