# Plan: GitHub Changelog Email Digest Automation

Build a Python script that fetches GitHub's changelog RSS feed daily, categorizes entries (releases, improvements, retirements), skips sending if no new content since last digest, and includes demo outlines with click-by-click navigation for new releases.

## Project Structure

```
src/
  main.py          # orchestration + CLI entry point
  changelog.py     # fetch, parse, and categorize RSS feed
  email.py         # build HTML + send via SendGrid
  state.py         # load/save processed URLs set
templates/
  digest_email.html
.github/
  workflows/
    digest.yml
requirements.txt
```

## Steps

1. **Create changelog module** in `src/changelog.py`:
   - Fetch RSS from `https://github.blog/changelog/feed/` using `feedparser`
   - Parse entries and categorize by `<category domain="changelog-type">` tags into: `Release`, `Improvement`, `Retired`
   - No date filtering — rely entirely on processed URLs set to determine what's new

2. **Implement state manager** in `src/state.py`:
   - Maintain a **set of processed entry URLs** (no timestamps needed)
   - On each run: filter out any entries whose URLs already exist in the set
   - After sending: add all newly included entry URLs to the set
   - Skip email entirely if no unsent entries exist
   - **Storage**: Use GitHub Actions cache with a fixed key (`changelog-state-v1`) — avoids commit noise
   - Schema: `{"processed_urls": ["https://github.blog/changelog/...", ...]}`

3. **Create demo outline extractor** in `src/changelog.py` (same module):
   - For each Release entry, parse `content:encoded` HTML using BeautifulSoup
   - Extract the official `docs.github.com` link (GitHub always includes one)
   - Scrape the docs page for "How to access" or navigation sections
   - Fall back to template: `"Settings → [Feature Area] → [Feature Name]"` if no path found
   - **No AI dependency** — deterministic extraction is more reliable and cost-free

4. **Build email module** in `src/email.py` + `templates/digest_email.html`:
   - Use Jinja2 to render a beautifully designed HTML email
   - **Visual Design**: Modern, clean layout inspired by premium newsletters (Substack, Morning Brew)
   - **Color Palette**: GitHub-inspired colors — dark header (#0d1117), accent purple (#8957e5), green for releases (#238636), blue for improvements (#1f6feb), orange for retirements (#d29922)
   - **Typography**: System font stack (`-apple-system, BlinkMacSystemFont, Segoe UI, Roboto`), 24px headers, 16px body
   - **Section Cards**: Rounded cards (8px radius) with subtle shadows
   - **Icons/Badges**: Emoji icons (🚀 releases, ✨ improvements, 🔄 retirements) with colored badges
   - **Demo Outline Styling**: Numbered steps, code blocks for CLI commands, highlighted navigation paths (`Settings → Copilot → Memory`)
   - **Responsive Design**: Mobile-first, 600px max-width container
   - **Header/Footer**: Branded header, footer with digest date range
   - **Whitespace**: Generous padding (24px sections, 16px cards)
   - **Dark Mode Support**: `@media (prefers-color-scheme: dark)` styles
   - Send via SendGrid API

5. **Create main orchestrator** in `src/main.py`:
   - Load state from cache/file
   - Fetch and categorize changelog entries
   - Filter to only unsent entries
   - If empty → exit without sending
   - Generate demo outlines for releases
   - Render and send email
   - Save updated state

6. **Set up GitHub Actions workflow** in `.github/workflows/digest.yml`:
   - Schedule: `cron: '0 16 * * *'` (8 AM PST = 4 PM UTC)
   - Use `actions/cache` to persist state between runs
   - Repository secrets: `SENDGRID_API_KEY`, `SENDGRID_FROM_EMAIL`, `DIGEST_TO_EMAIL`

## Configuration

Environment variables:
- `SENDGRID_API_KEY` — SendGrid API key
- `SENDGRID_FROM_EMAIL` — Verified sender email
- `DIGEST_TO_EMAIL` — Recipient email address(es)

## Dependencies

```
feedparser
beautifulsoup4
requests
jinja2
sendgrid
```
