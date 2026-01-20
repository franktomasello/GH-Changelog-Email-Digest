# Plan: GitHub Changelog Email Digest Automation

Build a Python script that fetches GitHub's changelog RSS feed daily, categorizes entries (releases, improvements, retirements), skips sending if no new content since last digest, and includes AI-generated demo outlines with click-by-click navigation for new releases.

## Steps

1. **Create core feed parser** in `src/feed_parser.py` — fetch RSS from `https://github.blog/changelog/feed/`, parse with `feedparser`, filter entries from last 7 days using `pubDate` field.

2. **Build categorizer** in `src/categorizer.py` — sort entries by `<category domain="changelog-type">` tags into three lists: `Release`, `Improvement`, `Retired`.

3. **Implement state manager** in `src/state_manager.py` — read/write `data/last_sent.json` to:
   - Track last digest timestamp
   - **Store a set of all previously sent entry URLs** to ensure no item is ever repeated across digests
   - On each run, filter out any entries whose URLs already exist in the "sent" set
   - After sending, append all newly included entry URLs to the persistent set
   - Skip email entirely if no *unsent* entries exist (even if there are entries from last 7 days that were already covered)
   - Schema: `{"last_sent": "2026-01-19T16:00:00Z", "sent_entry_urls": ["https://github.blog/changelog/...", ...]}`

4. **Create demo outline generator** in `src/demo_outline.py` — for each Release entry, parse `content:encoded` HTML to extract docs.github.com links, settings paths (e.g., "Settings > Copilot > Memory"), and use OpenAI/Claude API to generate step-by-step navigation instructions.

5. **Build email renderer** in `src/email_builder.py` + `templates/digest_email.html` — use Jinja2 to render a beautifully designed HTML email:
   - **Visual Design**: Modern, clean layout inspired by premium newsletters (Substack, Morning Brew)
   - **Color Palette**: GitHub-inspired colors — dark header (#0d1117), accent purple (#8957e5), green for releases (#238636), blue for improvements (#1f6feb), orange for retirements (#d29922)
   - **Typography**: System font stack for reliability (`-apple-system, BlinkMacSystemFont, Segoe UI, Roboto`), clear hierarchy with 24px headers, 16px body
   - **Section Cards**: Each changelog item in a rounded card (8px radius) with subtle shadows and hover-ready borders
   - **Icons/Badges**: Inline SVG icons for each category (🚀 releases, ✨ improvements, 🔄 retirements) with colored badges
   - **Demo Outline Styling**: Collapsible accordion-style sections with numbered steps, code blocks for CLI commands, and highlighted navigation paths (e.g., `Settings → Copilot → Memory`)
   - **Responsive Design**: Mobile-first approach, single-column layout that looks great on all devices (600px max-width container)
   - **Header/Footer**: Branded header with GitHub Changelog logo, footer with unsubscribe link and digest date range
   - **Whitespace**: Generous padding (24px sections, 16px card padding) for readability
   - **Dark Mode Support**: Include `@media (prefers-color-scheme: dark)` styles for email clients that support it

6. **Set up GitHub Actions workflow** in `.github/workflows/digest.yml` — schedule at `cron: '0 16 * * *'` (8 AM PST = 4 PM UTC), use repository secrets for `SENDGRID_API_KEY` and `OPENAI_API_KEY`.

## Further Considerations

1. **Email Provider**: SendGrid (100 free emails/day, simple API) / AWS SES (cheaper at scale) / Gmail SMTP (easiest for personal use but less reliable)?

2. **Demo Outline Generation**: Use AI (OpenAI/Claude) to generate navigation paths from changelog content, or extract only from embedded docs links and rely on manual review?

3. **State Persistence**: Commit `last_sent.json` back to repo after each run, or use GitHub repository variables / Gist API for external storage?
