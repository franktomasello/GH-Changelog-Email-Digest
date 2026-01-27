<div align="center">

# 🚀 GitHub Changelog Email Digest

**Your daily briefing on what's new in GitHub — delivered at 8 AM PT**

[![GitHub Actions](https://img.shields.io/badge/Automation-GitHub%20Actions-2088FF?style=for-the-badge&logo=github-actions&logoColor=white)](https://github.com/features/actions)
[![Gmail SMTP](https://img.shields.io/badge/Email-Gmail%20SMTP-EA4335?style=for-the-badge&logo=gmail&logoColor=white)](https://support.google.com/mail/answer/7126229)
[![Python](https://img.shields.io/badge/Python-3.9+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)

<br />

<img src="https://github.githubassets.com/images/modules/logos_page/GitHub-Mark.png" alt="GitHub" width="80" />

<br />

*Built for Solutions Engineers who need to stay on top of GitHub's latest releases*

---

[Features](#-features) • [Quick Start](#-quick-start) • [How It Works](#-how-it-works) • [Configuration](#%EF%B8%8F-configuration) • [Project Structure](#-project-structure)

</div>

<br />

## ✨ Features

<table>
<tr>
<td width="50%">

### 📬 Smart Email Digest
- **Dark mode** premium design
- **Responsive** tables for all email clients
- **Pacific Time** formatted dates
- **No duplicates** — each entry sent once

</td>
<td width="50%">

### 🎯 SE-Focused Content
- **Concise summaries** — the stuff that matters
- **Demo outlines** — click-by-click navigation
- **Accurate docs links** — web search powered
- **Key features** — bullet points for demos

</td>
</tr>
<tr>
<td width="50%">

### 📊 Organized Categories
- 🚀 **New Releases** — with demo guidance
- ✨ **Improvements** — enhancements
- � **Retirements** — deprecations

</td>
<td width="50%">

### ⚡ Fully Automated
- **Daily at 8 AM PT** via GitHub Actions
- **Manual triggers** available
- **Test email mode** — send to yourself only
- **Dry run mode** for testing
- **State persistence** in JSON

</td>
</tr>
</table>

<br />

## 🚀 Quick Start

### Prerequisites

- Python 3.9+
- Gmail account with [App Password](https://myaccount.google.com/apppasswords) (free — 500 emails/day)

### 1️⃣ Clone & Install

```bash
git clone https://github.com/yourusername/GH-Changelog-Email-Digest.git
cd GH-Changelog-Email-Digest
pip install -r requirements.txt
```

### 2️⃣ Configure Environment

```bash
export SMTP_HOST=smtp.gmail.com
export SMTP_PORT=587
export SMTP_USER=your@gmail.com
export SMTP_PASSWORD=your_app_password
export SMTP_FROM_EMAIL=your@gmail.com
export DIGEST_TO_EMAIL=recipient@email.com
```

### 3️⃣ Run

```bash
cd src && python main.py
```

<details>
<summary><strong>📋 CLI Options</strong></summary>

```bash
python main.py              # Send digest (if new entries exist)
python main.py --dry-run    # Process without sending email
python main.py --force      # Send even with no new entries
python main.py --all        # Include all entries from past week
python main.py --preview    # Output HTML to stdout
```

</details>

<br />

## 🔄 How It Works

```
══════════════════════════════════════════════════════════════════════════════
                               DAILY @ 8 AM PT                                
══════════════════════════════════════════════════════════════════════════════
                                                                              
   ① FETCH              ② PARSE               ③ DEDUPE                      
   ┈┈┈┈┈┈┈┈┈┈┈┈┈┈       ┈┈┈┈┈┈┈┈┈┈┈┈┈┈        ┈┈┈┈┈┈┈┈┈┈┈┈┈┈                  
   GitHub Changelog     Extract titles,       Check against                   
   RSS Feed             dates, content        state.json                      
   (atom.xml)           via feedparser        for seen URLs                   
                                                                              
══════════════════════════════════════════════════════════════════════════════
                                                                              
   ④ CATEGORIZE         ⑤ ENRICH              ⑥ SEARCH DOCS                 
   ┈┈┈┈┈┈┈┈┈┈┈┈┈┈       ┈┈┈┈┈┈┈┈┈┈┈┈┈┈        ┈┈┈┈┈┈┈┈┈┈┈┈┈┈                  
   🚀 Releases          Extract key           Multi-strategy:                 
   ✨ Improvements      features & SE-        • Embedded links                
   🔌 Retirements       focused summary       • Web search                    
                        (~350 chars)          • Keyword mapping               
                                                                              
══════════════════════════════════════════════════════════════════════════════
                                                                              
   ⑦ RENDER             ⑧ SEND                ⑨ PERSIST                     
   ┈┈┈┈┈┈┈┈┈┈┈┈┈┈       ┈┈┈┈┈┈┈┈┈┈┈┈┈┈        ┈┈┈┈┈┈┈┈┈┈┈┈┈┈                  
   Jinja2 template      Gmail SMTP            Git commit                      
   Dark mode HTML       (free tier)           state.json                      
   Table-based CSS      Any recipient         [skip ci]                       
                                                                              
══════════════════════════════════════════════════════════════════════════════
```

### 🔍 Smart Documentation Search

The digest intelligently finds accurate documentation for each entry using a **3-tier fallback strategy**:

| Priority | Method | Description |
|:--------:|:-------|:------------|
| 1️⃣ | **Embedded Links** | Extracts `docs.github.com` URLs directly from changelog HTML |
| 2️⃣ | **Web Search** | Queries `site:docs.github.com` with title keywords |
| 3️⃣ | **Keyword Mapping** | 20+ curated feature → docs URL mappings (Copilot, Actions, Security, Codespaces, Projects, etc.) |

### 🎯 SE-Focused Demo Outlines

Each entry includes an **actionable demo outline** with:
- **Concise summary** (~350 chars) highlighting business value
- **Top 4 key features** extracted from the actual release
- **Direct documentation link** for deeper exploration

<br />

## ⚙️ Configuration

### GitHub Actions (Automated)

The workflow runs **daily at 8 AM Pacific Time**.

#### Repository Secrets

Navigate to **Settings → Secrets and variables → Actions**:

| Secret | Description |
|:-------|:------------|
| `SMTP_HOST` | SMTP server (e.g., `smtp.gmail.com`) |
| `SMTP_PORT` | SMTP port (e.g., `587`) |
| `SMTP_USER` | Your Gmail address |
| `SMTP_PASSWORD` | Gmail App Password |
| `SMTP_FROM_EMAIL` | Sender email address |
| `DIGEST_TO_EMAIL` | Recipient email(s), comma-separated |

#### Manual Trigger

Go to **Actions → GitHub Changelog Digest → Run workflow** with options:
- 📧 **Test email** — send only to this address (leave empty for all recipients)
- ☑️ **Dry run** — test without sending
- ☑️ **Force** — send even with no new entries

<br />

## 📁 Project Structure

```
GH-Changelog-Email-Digest/
│
├── 📂 .github/
│   └── workflows/
│       └── digest.yml              # ⏰ Daily cron job (8 AM PT)
│
├── 📂 src/
│   ├── main.py                     # 🎯 Entry point & orchestration
│   ├── changelog.py                # 📡 RSS fetch, parse, docs search
│   ├── email_sender.py             # 📧 Build & send via SMTP
│   └── state.py                    # 💾 Track processed entries
│
├── 📂 templates/
│   └── digest_email.html           # 🎨 Jinja2 email template (dark mode, GitHub Octicons)
│
├── 📂 data/
│   └── state.json                  # 📋 Persisted URLs (auto-generated)
│
├── requirements.txt                # 📦 Python dependencies
└── README.md                       # 📖 You are here
```

<br />

## 🛠️ Tech Stack

<div align="center">

| Component | Technology |
|:---------:|:----------:|
| **Language** | ![Python](https://img.shields.io/badge/Python-3776AB?style=flat-square&logo=python&logoColor=white) |
| **Email** | ![Gmail SMTP](https://img.shields.io/badge/Gmail%20SMTP-EA4335?style=flat-square&logo=gmail&logoColor=white) |
| **Automation** | ![GitHub Actions](https://img.shields.io/badge/GitHub%20Actions-2088FF?style=flat-square&logo=github-actions&logoColor=white) |
| **Parsing** | `feedparser` · `beautifulsoup4` |
| **Templating** | `jinja2` |

</div>

<br />

## 📄 License

MIT License — feel free to use and modify.

<br />

---

<div align="center">

**[⬆ Back to Top](#-github-changelog-email-digest)**

<br />

[Report Bug](https://github.com/franktomasello/GH-Changelog-Email-Digest/issues) · [Request Feature](https://github.com/franktomasello/GH-Changelog-Email-Digest/issues)

</div>
