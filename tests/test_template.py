"""Rendering tests for templates/digest_email.html: the visual design's
contract (what each card shows and how), plus Outlook-safety invariants."""

import re

import pytest

import email_sender as es


def item(title="Feature shipped", **kw):
    base = {"title": title, "url": "https://github.blog/changelog/x", "published": "Oct 22, 2026",
            "summary": "A summary.", "category": "releases", "labels": ["Copilot", "Actions"],
            "docs_url": "https://docs.github.com/x", "key_features": []}
    base.update(kw)
    return base


def render(releases=(), improvements=(), retirements=()):
    return es.build_email_html(list(releases), list(improvements), list(retirements),
                               digest_date="Saturday, October 24, 2026")


def test_meta_line_is_date_without_year_then_labels():
    html = render([item()])
    assert re.search(r'class="card-meta"[^>]*>Oct 22<span[^>]*>&nbsp;&nbsp;&middot;&nbsp; </span>'
                     r'<span[^>]*>Copilot</span>', html)
    assert "card-label" not in html and "Oct 22, 2026" not in html


def test_title_is_plain_with_no_trailing_arrow():
    html = render([item("Usage metrics API adds usage from tools")])
    assert ">Usage metrics API adds usage from tools</a>" in html
    assert "card-arrow" not in html


def test_bold_lead_ins_when_every_bullet_has_one():
    html = render([item(key_features=["Request count: successful requests", "Token usage: tokens used"])])
    assert '<span style="color: #c9d1d9; font-weight: 600;">Request count:</span> successful requests' in html


def test_no_bold_lead_ins_when_any_bullet_lacks_one():
    # All-or-nothing, so a card never mixes bold and plain bullets.
    html = render([item(key_features=["Request count: successful requests", "Plain phrase bullet"])])
    assert "font-weight: 600;\">Request count:</span>" not in html
    assert "Request count: successful requests" in html


def test_bullet_dots_take_the_section_colour():
    html = render(improvements=[item(key_features=["One"])])
    assert re.search(r'color: #58a6ff;">&bull;</td>', html)


def test_empty_category_tile_is_dimmed_with_muted_icon():
    html = render([item()], [item()], [])
    assert "retirements-muted.png" in html and "retirements-amber.png" not in html
    assert "releases-green.png" in html and "improvements-blue.png" in html
    assert 'class="stat-card stat-card-amber"' not in html  # no hover accent on an empty tile
    assert 'class="stat-card stat-card-green"' in html


def test_header_carries_the_total_inline():
    assert ">10</span>&nbsp;updates" in render([item()] * 10)
    assert ">1</span>&nbsp;update<" in render([item()])


def test_section_eyebrows_and_spacing_classes():
    html = render([item()], [item()], [item()])
    for name in ("Releases", "Improvements", "Retirements"):
        assert re.search(rf'text-transform: uppercase;[^>]*>{name}<span', html)
    classes = re.findall(r'class="(section-header[^"]*)"', html)
    assert classes == ["section-header", "section-header section-header-later",
                       "section-header section-header-later"]  # not on the first section
    assert re.findall(r'class="(section-header[^"]*)"', render(improvements=[item()])) == ["section-header"]


def test_no_back_to_top_controls():
    html = render([item()], [item()], [item()])
    assert "Back to top" not in html and "#email-top" not in html


def test_docs_row_only_when_there_is_a_docs_link():
    html = render([item(docs_url="")])
    assert "Read the docs" not in html and 'card-content-docs"' not in html
    assert "Read the docs&nbsp;&rarr;</a>" in render([item()])


def test_empty_state_still_renders():
    assert "Nothing new in the changelog today." in render()


@pytest.mark.parametrize("data", [((), (), ()), ([item()], [item()], [item()]), ([item()], [], [])])
def test_outlook_conditional_comments_balance(data):
    html = render(*data)
    opens = len(re.findall(r"<!--\[if [^\]]*mso[^\]]*\]>", html))
    assert opens == len(re.findall(r"<!\[endif\]-->", html))


def test_footer_mso_wrapper_brackets_the_button_row():
    # Regression: the MSO close used to sit inside the first button cell.
    html = render([item()])
    footer = html[html.index('class="footer-links"'):]
    assert footer.index("RSS feed") < footer.index("<![endif]")
