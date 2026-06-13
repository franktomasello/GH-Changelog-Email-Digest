<div align="center">

<img src="https://avatars.githubusercontent.com/u/9919?s=200&v=4" alt="GitHub" width="64" height="64">

# GitHub Changelog Digest

**A daily email that turns the GitHub Changelog into a field-ready briefing.**

[![Python](https://img.shields.io/badge/Python-3.9%2B-c9d1d9?style=flat-square&logo=python&logoColor=white&labelColor=161b22)](https://python.org)
[![GitHub Actions](https://img.shields.io/badge/Automated-GitHub%20Actions-c9d1d9?style=flat-square&logo=githubactions&logoColor=white&labelColor=161b22)](https://github.com/features/actions)
[![SMTP](https://img.shields.io/badge/Email-SMTP-c9d1d9?style=flat-square&logo=gmail&logoColor=white&labelColor=161b22)](https://support.google.com/mail/answer/7126229)
[![License](https://img.shields.io/badge/License-MIT-c9d1d9?style=flat-square&labelColor=161b22)](#license)

Built for **GitHub Solutions Engineers and Sales Reps** — know what shipped before the customer asks.

[Who it's for](#who-its-for) · [Quick start](#quick-start) · [How it works](#how-it-works) · [Configuration](#configuration) · [Structure](#project-structure)

</div>

<br />

## Who it's for

This digest is built for the **field** — the people who need to know what GitHub shipped before it comes up on a call or in a demo. It runs every day and distills the changelog into one email (and stays quiet on days with nothing new). Each entry is written to serve two audiences at once:

- **Solutions Engineers** — every entry lists the top features worth showing plus a relevance-checked docs link, so you can turn a release into a demo quickly.
- **Sales Reps** — every entry leads with a concise, plain-language summary of what shipped, so you can speak to it on a call without reading the release notes.

One email. No duplicates — each update is sent exactly once.

<br />

## Features

| | |
|---|---|
| **Field-ready content** | A concise summary, the top features, and a relevance-checked docs link on every entry — enough to talk through or demo |
| **Accurate docs links** | Each docs link is fetched and checked against the page content, and omitted when there's no confident match |
| **GitHub-native design** | Dark Primer theme, Mona Sans, Octicons — responsive, with explicit Outlook/Word-engine handling |
| **Organized by impact** | Releases, Improvements, and Retirements, each in its own section |
| **Fully automated** | Runs daily on GitHub Actions, with test, dry-run, and force modes |
| **No repeats** | Sent entries are tracked in state, so nobody gets the same update twice |

<br />

## Quick start

**Prerequisites** — Python 3.9+ and an SMTP account (e.g. a Gmail [App Password](https://myaccount.google.com/apppasswords), free for up to 500 emails/day).

```bash
git clone https://github.com/franktomasello/GH-Changelog-Email-Digest.git
cd GH-Changelog-Email-Digest
pip install -r requirements.txt
```

Configure the environment:

```bash
export SMTP_HOST=smtp.gmail.com
export SMTP_PORT=587
export SMTP_USER=you@gmail.com
export SMTP_PASSWORD=your_app_password
export SMTP_FROM_EMAIL=you@gmail.com
export DIGEST_TO_EMAIL=recipient@example.com   # comma-separated for multiple
```

Run it:

```bash
cd src && python main.py
```

<details>
<summary><strong>Command-line options</strong></summary>

<br />

| Command | What it does |
|---|---|
| `python main.py` | Send the digest if there are new entries |
| `python main.py --dry-run` | Process everything but don't send or update state |
| `python main.py --force` | Send even when there are no new entries |
| `python main.py --all` | Re-include entries already sent, ignoring dedup state (the past-week window still applies). Manual workflow runs use this by default |
| `python main.py --preview` | Print the rendered HTML to stdout |

</details>

<br />

## How it works

Each run walks the same pipeline:

```
Fetch  →  Parse  →  Dedupe  →  Categorize  →  Enrich  →  Render  →  Send  →  Persist
```

| Stage | Detail |
|---|---|
| **Fetch** | Pull the GitHub Changelog RSS feed |
| **Parse** | Extract titles, dates, and content with `feedparser` |
| **Dedupe** | Drop anything already recorded in `state.json` |
| **Categorize** | Sort into Releases, Improvements, and Retirements |
| **Enrich** | Build the concise summary and key features; find and verify the docs link |
| **Render** | Compose the HTML with the Jinja2 template |
| **Send** | Deliver over SMTP to each recipient individually |
| **Persist** | Commit the updated `state.json` so nothing repeats |

### Documentation lookup

Each entry's docs link is resolved in two tiers, and every candidate is fetched and checked for relevance before it's used. Links prefer GitHub's Enterprise Cloud / Server docs:

| Tier | Method | Detail |
|:--:|---|---|
| 1 | **The entry's own embedded docs link** | Used as-is if it's already an Enterprise link; otherwise converted to a verified Enterprise equivalent, or used as the general docs link |
| 2 | **Search GitHub Docs** | Search the entry's keywords — Enterprise docs first, then general |

If no candidate is confirmed relevant, the entry shows **no docs link** rather than a wrong one.

### What's in each entry

Each item carries just enough to act on — for both the demo and the conversation:

- A **concise summary** (a few sentences) of what the update is.
- Up to **four key features** pulled from the entry — what an SE would show.
- A **verified docs link**, when one is confidently matched.

Dates are shown in Pacific Time.

<br />

## Configuration

### Recipients

Recipients live in the **`DIGEST_TO_EMAIL`** secret — a comma-separated list. The digest goes to everyone on it.

- **Add or remove someone** — edit `DIGEST_TO_EMAIL` under **Settings → Secrets and variables → Actions** (the value is the full comma-separated list).
- **Send a one-off test** — trigger a manual run and fill in the **Test email** input; it overrides the list for that run only.

Keeping recipients in the secret (rather than a tracked file) means addresses never land in public git history.

### Repository secrets

**Settings → Secrets and variables → Actions:**

| Secret | Description |
|---|---|
| `SMTP_HOST` | SMTP server (e.g. `smtp.gmail.com`) |
| `SMTP_PORT` | SMTP port (e.g. `587`) |
| `SMTP_USER` | SMTP username |
| `SMTP_PASSWORD` | SMTP password / app password |
| `SMTP_FROM_EMAIL` | Sender address |
| `DIGEST_TO_EMAIL` | Recipient address(es), comma-separated |

### Automation

The workflow runs **daily** via GitHub Actions. To run it by hand: **Actions → GitHub Changelog Digest → Run workflow**, with optional inputs for a **test email**, a **dry run**, or a **force** send.

<br />

## Project structure

```
GH-Changelog-Email-Digest/
├── .github/workflows/
│   └── digest.yml            # Daily cron + manual trigger
├── assets/icons/             # Octicon PNGs used in the email tiles
├── src/
│   ├── __init__.py           # Marks src as a package
│   ├── main.py               # Entry point and orchestration
│   ├── changelog.py          # RSS fetch, parse, and docs lookup
│   ├── email_sender.py       # Build the email (HTML + plain text) and send over SMTP
│   └── state.py              # Track which entries have been sent
├── templates/
│   └── digest_email.html     # Jinja2 email (dark Primer theme, Mona Sans)
├── data/
│   └── state.json            # Persisted sent-URLs (auto-generated)
└── requirements.txt
```

<br />

## Tech stack

**Python** · **GitHub Actions** (automation) · **SMTP** (delivery) · `feedparser` + `beautifulsoup4` (parsing) · `jinja2` (templating)

<br />

## License

MIT — free to use and modify.

<div align="center">
<br />

[Report a bug](https://github.com/franktomasello/GH-Changelog-Email-Digest/issues) · [Request a feature](https://github.com/franktomasello/GH-Changelog-Email-Digest/issues)

</div>
