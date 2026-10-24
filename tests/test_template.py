"""Structural tests for templates/digest_email.html: Outlook-safety invariants."""

import re

import pytest

import email_sender as es


def item(**kw):
    base = {"title": "Feature shipped", "url": "https://github.blog/changelog/x", "published": "Oct 22, 2026",
            "summary": "A summary.", "category": "releases", "labels": ["Copilot"],
            "docs_url": "https://docs.github.com/x", "key_features": ["One feature"]}
    base.update(kw)
    return base


def render(releases=(), improvements=(), retirements=()):
    return es.build_email_html(list(releases), list(improvements), list(retirements),
                               digest_date="Saturday, October 24, 2026")


@pytest.mark.parametrize("data", [((), (), ()), ([item()], [item()], [item()]), ([item()], [], [])])
def test_outlook_conditional_comments_balance(data):
    html = render(*data)
    opens = len(re.findall(r"<!--\[if [^\]]*mso[^\]]*\]>", html))
    assert opens == len(re.findall(r"<!\[endif\]-->", html))


def test_footer_mso_wrapper_brackets_the_button_row():
    # Regression: the Windows-Outlook wrapper's close sat inside the first
    # footer button cell, closing the wrapper table before the second button.
    html = render([item()])
    footer = html[html.index('class="footer-links"'):]
    assert footer.index("RSS feed") < footer.index("<![endif]")
