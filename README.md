<div align="center">

# 📬 GH-Changelog-Email-Digest

### Stay Updated with GitHub's Latest Features! 🚀

*Never miss a GitHub product update again. Get a curated email digest of the latest changelog entries delivered straight to your inbox.*

[![GitHub](https://img.shields.io/badge/GitHub-Changelog-blue?logo=github)](https://github.blog/changelog/)
[![Email](https://img.shields.io/badge/Digest-Email-green?logo=gmail)](#)
[![Automation](https://img.shields.io/badge/Automation-Enabled-orange?logo=github-actions)](https://github.com/features/actions)

---

</div>

## 🌟 Overview

**GH-Changelog-Email-Digest** is an automated service that monitors the [GitHub product changelog](https://github.blog/changelog/) and sends you regular email digests summarizing what's new. Stay informed about new features, improvements, bug fixes, and important updates without having to manually check the changelog.

Perfect for:
- 👨‍💻 Developers who want to stay current with GitHub features
- 👥 Teams that need to know about platform updates
- 🏢 Organizations managing GitHub Enterprise
- 🎓 Educators teaching with GitHub
- 📊 Product managers tracking GitHub capabilities

## ✨ Features

- **🔄 Automated Monitoring**: Continuously checks the GitHub changelog for new entries
- **📧 Email Delivery**: Sends formatted digest emails to your inbox
- **⏰ Customizable Schedule**: Configure how often you want to receive updates (daily, weekly, etc.)
- **📝 Clean Formatting**: Well-structured emails that are easy to read and scan
- **🎯 Zero Maintenance**: Set it up once and let it run automatically
- **🔔 Never Miss an Update**: Get notified about important GitHub product changes

## 🎯 How It Works

```
┌─────────────────┐
│  GitHub Blog    │
│   Changelog     │──┐
│   RSS Feed      │  │
└─────────────────┘  │
                     │ Fetch
                     │ Updates
┌─────────────────┐  │
│  GH-Changelog   │◄─┘
│  Email Digest   │
│                 │──┐
└─────────────────┘  │
                     │ Process &
                     │ Format
┌─────────────────┐  │
│  Email Service  │◄─┘
│  (SMTP/API)     │
│                 │──┐
└─────────────────┘  │
                     │ Deliver
┌─────────────────┐  │
│  Your Inbox 📬  │◄─┘
│                 │
└─────────────────┘
```

The service:
1. Polls the GitHub changelog RSS feed at regular intervals
2. Identifies new entries since the last check
3. Formats the updates into a clean, readable email digest
4. Sends the digest to your configured email address(es)

## 🚀 Getting Started

### Prerequisites

- Node.js (v14 or higher) or Python (v3.8 or higher)
- An email service account (Gmail, SendGrid, AWS SES, etc.)
- SMTP credentials or API keys for sending emails

### Installation

```bash
# Clone the repository
git clone https://github.com/franktomasello/GH-Changelog-Email-Digest.git

# Navigate to the project directory
cd GH-Changelog-Email-Digest

# Install dependencies
npm install  # or pip install -r requirements.txt
```

### Configuration

1. **Set up your environment variables:**

Create a `.env` file in the project root:

```env
# Email Configuration
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@gmail.com
SMTP_PASSWORD=your-app-password

# Recipient Configuration
RECIPIENT_EMAIL=recipient@example.com
RECIPIENT_NAME=Your Name

# Schedule Configuration (cron format)
DIGEST_SCHEDULE=0 9 * * 1  # Every Monday at 9 AM

# GitHub Changelog Feed
CHANGELOG_FEED_URL=https://github.blog/changelog/feed/
```

2. **Configure your email provider:**

<details>
<summary>📧 Gmail Setup</summary>

1. Enable 2-factor authentication on your Google account
2. Generate an [App Password](https://myaccount.google.com/apppasswords)
3. Use the app password in your `.env` file

</details>

<details>
<summary>📨 SendGrid Setup</summary>

1. Sign up for a [SendGrid account](https://sendgrid.com/)
2. Generate an API key from the SendGrid dashboard
3. Update your `.env` with SendGrid settings:

```env
SMTP_HOST=smtp.sendgrid.net
SMTP_PORT=587
SMTP_USER=apikey
SMTP_PASSWORD=your-sendgrid-api-key
FROM_EMAIL=noreply@yourdomain.com
FROM_NAME=GitHub Changelog Digest
```

</details>

<details>
<summary>☁️ AWS SES Setup</summary>

1. Set up [AWS SES](https://aws.amazon.com/ses/) and verify your domain/email
2. Create IAM credentials with SES send permissions
3. Configure AWS credentials in your `.env`:

```env
AWS_ACCESS_KEY_ID=your-access-key-id
AWS_SECRET_ACCESS_KEY=your-secret-access-key
AWS_REGION=us-east-1
FROM_EMAIL=noreply@yourdomain.com
FROM_NAME=GitHub Changelog Digest
```

</details>

### Running the Service

**Run once (manual):**
```bash
npm start  # or python main.py
```

**Run as a scheduled service:**
```bash
# Using cron (Linux/Mac)
crontab -e
# Add: 0 9 * * 1 cd /path/to/GH-Changelog-Email-Digest && npm start >> /var/log/gh-digest.log 2>&1

# Using GitHub Actions (see .github/workflows/digest.yml)
# Automatically runs on schedule

# Using systemd (Linux)
sudo systemctl enable gh-changelog-digest
sudo systemctl start gh-changelog-digest
```

## 📋 Usage Examples

### Example Email Digest

```
Subject: GitHub Changelog Digest - This Week's Updates

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📬 GitHub Changelog Digest
New Updates from the Past Week
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🎉 NEW FEATURES

• GitHub Copilot now supports Python type hints
  Enhanced code completion with better type inference
  [Link to changelog entry]

• Actions: Larger runners now available in Free tier
  Get 2x compute power for open source projects
  [Link to changelog entry]

🔧 IMPROVEMENTS

• GitHub Mobile: Improved code review experience
  Faster loading and better syntax highlighting
  [Link to changelog entry]

🐛 BUG FIXES

• Resolved issue with branch protection rules
  Settings now save correctly on all repository types
  [Link to changelog entry]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
View all updates: https://github.blog/changelog/
```

## 🛠️ Configuration Options

| Option | Description | Default |
|--------|-------------|---------|
| `DIGEST_SCHEDULE` | Cron expression for digest frequency | `0 9 * * 1` (Monday 9 AM) |
| `MAX_ENTRIES` | Maximum changelog entries per digest | `20` |
| `LOOKBACK_DAYS` | Days to look back for changelog entries | `7` |
| `EMAIL_TEMPLATE` | HTML or plain text email template | `default` |
| `TIMEZONE` | Timezone for scheduling | `UTC` |

## 🤝 Contributing

Contributions are welcome! Here's how you can help:

1. 🍴 Fork the repository
2. 🌿 Create a feature branch (`git checkout -b feature/amazing-feature`)
3. 💾 Commit your changes (`git commit -m 'Add some amazing feature'`)
4. 📤 Push to the branch (`git push origin feature/amazing-feature`)
5. 🔀 Open a Pull Request

Please make sure to:
- Update documentation for any new features
- Add tests if applicable
- Follow the existing code style
- Keep commits focused and descriptive

## 📝 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- GitHub for providing the [changelog RSS feed](https://github.blog/changelog/feed/)
- All contributors who help improve this project
- The open source community

## 📞 Support & Contact

- 🐛 **Issues**: [GitHub Issues](https://github.com/franktomasello/GH-Changelog-Email-Digest/issues)
- 💬 **Discussions**: [GitHub Discussions](https://github.com/franktomasello/GH-Changelog-Email-Digest/discussions)
- 📧 **Email**: Create an issue for support questions

## 🗺️ Roadmap

- [ ] Support for filtering by category (Actions, Security, API, etc.)
- [ ] Multiple recipient support
- [ ] Slack/Discord integration
- [ ] Customizable email templates
- [ ] Web dashboard for managing subscriptions
- [ ] Digest preview before sending

---

<div align="center">

**Made with ❤️ by developers, for developers**

⭐ Star this repo if you find it useful!

[Report Bug](https://github.com/franktomasello/GH-Changelog-Email-Digest/issues) • [Request Feature](https://github.com/franktomasello/GH-Changelog-Email-Digest/issues)

</div>
