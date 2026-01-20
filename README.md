# GitHub Changelog Email Digest

Automated daily email digest of GitHub Changelog updates, sent at 8 AM PST.

## Features

- 🚀 **New Releases** — with demo outlines and click-by-click navigation paths
- ✨ **Improvements** — enhancements to existing features
- 🔄 **Retirements** — deprecated/removed features
- 📧 **Beautiful emails** — GitHub-inspired design, responsive, dark mode support
- 🔁 **No duplicates** — each changelog item is only sent once, ever

## Setup

### 1. Clone the repository

```bash
git clone https://github.com/yourusername/GH-Changelog-Email-Digest.git
cd GH-Changelog-Email-Digest
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure SendGrid

1. Create a [SendGrid account](https://signup.sendgrid.com/)
2. Create an [API key](https://app.sendgrid.com/settings/api_keys) with "Mail Send" permissions
3. [Verify a sender](https://app.sendgrid.com/settings/sender_auth) email address

### 4. Set environment variables

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

Or export directly:

```bash
export SENDGRID_API_KEY=your_api_key
export SENDGRID_FROM_EMAIL=verified-sender@example.com
export DIGEST_TO_EMAIL=recipient@example.com
```

### 5. Run locally

```bash
cd src
python main.py
```

#### CLI Options

```bash
python main.py --dry-run   # Process entries but don't send email
python main.py --force     # Send even if no new entries (for testing)
python main.py --preview   # Output HTML to stdout
```

## GitHub Actions (Automated Daily Digest)

The workflow runs automatically at 8 AM PST every day.

### Configure repository secrets

Go to **Settings → Secrets and variables → Actions** and add:

| Secret | Description |
|--------|-------------|
| `SENDGRID_API_KEY` | Your SendGrid API key |
| `SENDGRID_FROM_EMAIL` | Verified sender email |
| `DIGEST_TO_EMAIL` | Recipient email address |

### Manual trigger

You can also trigger the workflow manually from the **Actions** tab with options for:
- **Dry run** — test without sending email
- **Force** — send even if no new entries

## Project Structure

```
├── .github/
│   ├── prompts/
│   │   └── plan-ghChangelogEmailDigest.prompt.md
│   └── workflows/
│       └── digest.yml          # GitHub Actions workflow
├── src/
│   ├── main.py                 # Entry point & orchestration
│   ├── changelog.py            # RSS fetch, parse, categorize
│   ├── email.py                # Build & send emails
│   └── state.py                # Track processed entries
├── templates/
│   └── digest_email.html       # Jinja2 email template
├── data/
│   └── state.json              # Persisted state (auto-generated)
├── .env.example
├── requirements.txt
└── README.md
```

## How It Works

1. **Fetch** — Downloads the GitHub Changelog RSS feed
2. **Filter** — Removes entries already sent in previous digests
3. **Categorize** — Sorts entries into Releases, Improvements, Retirements
4. **Enrich** — Generates demo outlines for releases by:
   - Extracting `docs.github.com` links from content
   - Scraping docs pages for navigation paths
   - Falling back to smart templates based on labels
5. **Render** — Builds a beautiful HTML email with Jinja2
6. **Send** — Delivers via SendGrid API
7. **Save** — Persists processed entry URLs to prevent duplicates

## License

MIT

[Report Bug](https://github.com/franktomasello/GH-Changelog-Email-Digest/issues) • [Request Feature](https://github.com/franktomasello/GH-Changelog-Email-Digest/issues)

</div>
