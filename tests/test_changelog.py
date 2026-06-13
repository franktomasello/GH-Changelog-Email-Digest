"""Tests for changelog parsing, categorization, and the pure display helpers."""

from datetime import datetime, timezone

import pytest

import changelog as cl


def _entry(category="Improvement", title="t", labels=None):
    return cl.ChangelogEntry(
        title=title, url="https://x", published="p",
        published_dt=datetime.now(timezone.utc), summary="", content_html="",
        category=category, labels=labels or [],
    )


# --- categorization ----------------------------------------------------------

def test_categorize_routes_by_type():
    res = cl.categorize_entries([_entry("Release"), _entry("Retired"), _entry("Improvement")])
    assert len(res["releases"]) == 1
    assert len(res["retirements"]) == 1
    assert len(res["improvements"]) == 1


@pytest.mark.parametrize("term,bucket", [
    ("release", "releases"), ("RELEASE", "releases"), ("Release", "releases"),
    ("retired", "retirements"), ("Retired", "retirements"), ("deprecated", "retirements"),
    ("improvement", "improvements"), ("", "improvements"),
])
def test_categorize_is_case_insensitive(term, bucket):
    res = cl.categorize_entries([_entry(term)])
    assert len(res[bucket]) == 1


def test_categorize_unknown_term_falls_back_to_improvement(capsys):
    res = cl.categorize_entries([_entry("Frobnicated")])
    assert len(res["improvements"]) == 1
    assert "Unrecognized changelog-type" in capsys.readouterr().out


# --- _safe_href (scheme allowlist) ------------------------------------------

@pytest.mark.parametrize("url,expected", [
    ("https://docs.github.com/x", "https://docs.github.com/x"),
    ("http://example.com", "http://example.com"),
    ("javascript:alert(1)//docs.github.com", ""),
    ("data:text/html,<script>", ""),
    ("", ""),
    (None, ""),
])
def test_safe_href(url, expected):
    assert cl._safe_href(url) == expected


# --- label helpers -----------------------------------------------------------

def test_capitalize_label_titlecases_but_preserves_acronyms():
    assert cl._capitalize_label("collaboration tools") == "Collaboration Tools"
    assert cl._capitalize_label("API") == "API"


def test_fit_labels_keeps_short_labels():
    assert cl._fit_labels(["API", "Actions"]) == ["API", "Actions"]


def test_fit_labels_caps_at_max_count():
    assert cl._fit_labels(["A", "B", "C", "D"], max_count=3) == ["A", "B", "C"]


def test_fit_labels_drops_label_exceeding_width_budget():
    # A single ~40-char label estimates >240px and must not be emitted (it would
    # otherwise force horizontal overflow on a narrow phone).
    assert cl._fit_labels(["X" * 40]) == []


# --- convert_to_pst ----------------------------------------------------------

def test_convert_to_pst_formats_known_date():
    assert cl.convert_to_pst("Thu, 15 Jan 2026 21:57:44 +0000") == "Jan 15, 2026"


def test_convert_to_pst_returns_original_on_garbage():
    assert cl.convert_to_pst("not a date") == "not a date"


# --- validate_docs_url thresholds (strict vs non-strict) --------------------

class _FakeResp:
    def __init__(self, html):
        self.text = html
    def raise_for_status(self):
        pass


def _page(*words):
    body = " ".join(words)
    return f"<html><head><title>Docs</title></head><body>{body}</body></html>"


def test_validate_docs_url_threshold_boundary(monkeypatch):
    # Title keywords (after stop-word removal): copilot, code, review.
    title = "Copilot code review"

    # Page mentions only 1 of 3 keywords -> 0.33 ratio: passes non-strict (>=0.30),
    # fails strict (<0.60). This is exactly the embedded-vs-search trust boundary.
    monkeypatch.setattr(cl.requests, "get", lambda *a, **k: _FakeResp(_page("copilot")))
    assert cl.validate_docs_url("https://docs.github.com/x", title, strict=False) is True
    assert cl.validate_docs_url("https://docs.github.com/x", title, strict=True) is False

    # Page mentions all 3 -> 1.0 ratio: passes both.
    monkeypatch.setattr(cl.requests, "get", lambda *a, **k: _FakeResp(_page("copilot", "code", "review")))
    assert cl.validate_docs_url("https://docs.github.com/x", title, strict=True) is True


def test_validate_docs_url_returns_false_on_fetch_error(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(cl.requests, "get", boom)
    assert cl.validate_docs_url("https://docs.github.com/x", "anything here") is False


# --- entries_to_dict ---------------------------------------------------------

def test_entries_to_dict_shape_and_safe_url():
    e = _entry("Release", title="My Release", labels=["API"])
    e.url = "javascript:alert(1)"          # hostile scheme must be stripped
    e.docs_url = "https://docs.github.com/y"
    e.detailed_summary = "A clean summary."
    e.key_features = ["Feat A"]
    d = cl.entries_to_dict([e])[0]
    assert d["url"] == ""                   # scheme gate applied
    assert d["docs_url"] == "https://docs.github.com/y"
    assert d["summary"] == "A clean summary."
    assert d["key_features"] == ["Feat A"]
    assert d["labels"] == ["API"]
    # Dead keys from the old demo subsystem must be gone.
    for dead in ("demo_outline", "navigation_path", "demo_context", "all_links", "content_html"):
        assert dead not in d
