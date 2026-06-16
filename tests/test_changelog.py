"""Tests for changelog parsing, categorization, and the pure display helpers."""

import sys
import types
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


def test_capitalize_label_unescapes_html_entities():
    # Feed labels can carry a raw entity; unescape so the template doesn't render
    # a literal "&amp;" (double-escape).
    assert cl._capitalize_label("ecosystem &amp; accessibility") == "Ecosystem & Accessibility"


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
    monkeypatch.setattr(cl._DOCS_SESSION, "get", lambda *a, **k: _FakeResp(_page("copilot")))
    assert cl.validate_docs_url("https://docs.github.com/x", title, strict=False) is True
    assert cl.validate_docs_url("https://docs.github.com/x", title, strict=True) is False

    # Page mentions all 3 -> 1.0 ratio: passes both.
    monkeypatch.setattr(cl._DOCS_SESSION, "get", lambda *a, **k: _FakeResp(_page("copilot", "code", "review")))
    assert cl.validate_docs_url("https://docs.github.com/x", title, strict=True) is True


def test_validate_docs_url_returns_false_on_fetch_error(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(cl._DOCS_SESSION, "get", boom)
    assert cl.validate_docs_url("https://docs.github.com/x", "anything here") is False


# --- docs-link ranking (extract_best_docs_url) ------------------------------

def test_best_docs_url_prefers_release_notes_for_version_entry():
    # A whole-version GA entry should land on release notes, not a narrow feature.
    html = ('<a href="https://docs.github.com/enterprise-server@3.21/admin/release-notes">notes</a>'
            '<a href="https://docs.github.com/enterprise-server@3.21/admin/multiple-data-disks/configuring-multiple-data-disks">disks</a>')
    best = cl.extract_best_docs_url(html, ["enterprise", "server"])
    assert best.endswith("/admin/release-notes")


def test_best_docs_url_penalizes_rest_reference():
    # A REST API reference should lose to the feature's own page.
    html = ('<a href="https://docs.github.com/en/rest/actions/self-hosted-runners">rest</a>'
            '<a href="https://docs.github.com/en/actions/hosting-your-own-runners">hub</a>')
    best = cl.extract_best_docs_url(html, ["actions", "runners"])
    assert "hosting-your-own-runners" in best
    assert "/rest/" not in best


def test_best_docs_url_dedupes_keywords():
    # A keyword repeated in the summary must not multiply a URL's score and beat
    # a page that matches more *distinct* keywords.
    html = ('<a href="https://docs.github.com/en/a/repeated-word">A</a>'
            '<a href="https://docs.github.com/en/b/two-distinct">B</a>')
    best = cl.extract_best_docs_url(html, ["repeated", "repeated", "repeated", "two", "distinct"])
    assert best.endswith("/b/two-distinct")


# --- optional LLM docs-link picker ------------------------------------------

def test_llm_pick_docs_url_disabled_by_default(monkeypatch):
    monkeypatch.delenv("DIGEST_LLM", raising=False)
    monkeypatch.delenv("DIGEST_LLM_SUMMARIES", raising=False)
    assert cl.llm_pick_docs_url("Some title", "<p>body</p>") is None


def test_llm_pick_docs_url_returns_model_choice(monkeypatch):
    monkeypatch.setenv("DIGEST_LLM", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(cl, "_llm_call",
                        lambda *a, **k: "https://docs.github.com/en/actions/concepts/security/github_token")
    out = cl.llm_pick_docs_url("Agentic workflows no longer need a PAT", "<p>uses GITHUB_TOKEN</p>")
    assert out == "https://docs.github.com/en/actions/concepts/security/github_token"


def test_llm_pick_docs_url_handles_none(monkeypatch):
    monkeypatch.setenv("DIGEST_LLM", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(cl, "_llm_call", lambda *a, **k: "NONE")
    assert cl.llm_pick_docs_url("Obscure update", "<p>body</p>") is None


# --- key-feature bullet relevance -------------------------------------------

@pytest.mark.parametrize("bullet,offgoal", [
    ("$10 per active committer per month", True),
    ("GitHub Code Quality will become a paid product", True),
    ("AI-powered work, usage-based billing applies", True),
    ("If you are an existing customer, nothing changes today", True),
    ("/settings opens a full-screen settings dialog", False),
    ("Lock the runner setting so org defaults override repos", False),
])
def test_offgoal_bullet_filter(bullet, offgoal):
    assert cl._is_offgoal_bullet(bullet) is offgoal


def test_key_features_drops_pricing_and_short_headings():
    e = _entry("Release", title="GitHub Code Quality GA")
    e.content_html = (
        "<ul><li>Set up CodeQL-powered maintainability scans on your repositories</li>"
        "<li>$10 per active committer per month covers Code Quality findings</li></ul>"
        "<p><strong>Security</strong></p>"
    )
    feats = cl.extract_key_features(e)
    assert any("CodeQL-powered maintainability" in f for f in feats)   # real capability kept
    assert all("$10" not in f for f in feats)                          # pricing dropped
    assert "Security" not in feats                                     # one-word heading dropped


@pytest.mark.parametrize("bullet,vague", [
    ("New capabilities will be available", True),       # contentless filler
    ("More improvements coming soon", True),
    ("Additional features are available", True),
    ("Several enhancements to come", True),
    ("And more", True),
    ("Coming soon", True),
    ("More to come", True),
    ("Hierarchy view for GitHub Projects is now generally available", False),  # specific
    ("New repository rulesets for branch protection", False),
    ("More consistency across your data: reports line up with billing", False),
    ("More improvements to the audit log", False),      # has a concrete subject
    ("/settings reset restores the default for a setting", False),
])
def test_vague_bullet_filter(bullet, vague):
    assert cl._is_vague_bullet(bullet) is vague


def test_key_features_drops_vague_filler_keeps_specific():
    e = _entry("Release", title="GitHub Code Quality GA")
    e.content_html = (
        "<ul><li>New capabilities will be available</li>"
        "<li>CodeQL-powered maintainability and reliability scans run on Actions</li></ul>"
    )
    feats = cl.extract_key_features(e)
    assert any("CodeQL-powered" in f for f in feats)                   # specific kept
    assert all("New capabilities" not in f for f in feats)            # vague filler dropped


def test_llm_key_features_disabled_returns_none(monkeypatch):
    monkeypatch.delenv("DIGEST_LLM", raising=False)
    monkeypatch.delenv("DIGEST_LLM_SUMMARIES", raising=False)
    assert cl.llm_key_features("X", "<p>body here that is long enough</p>") is None


def test_llm_key_features_parses_lines(monkeypatch):
    monkeypatch.setenv("DIGEST_LLM", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(cl, "_llm_call",
                        lambda *a, **k: "- Open the settings dialog with /settings\n- Set a value inline")
    out = cl.llm_key_features("Copilot CLI /settings", "<p>...</p>")
    assert out == ["Open the settings dialog with /settings", "Set a value inline"]


def test_llm_key_features_empty_is_kept_not_fallback(monkeypatch):
    # Model judged no demoable surface -> returns [] (caller shows no bullets),
    # which is DISTINCT from None (disabled/failed -> caller uses heuristic).
    monkeypatch.setenv("DIGEST_LLM", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(cl, "_llm_call", lambda *a, **k: "")
    assert cl.llm_key_features("A pure retirement", "<p>body</p>") == []


def test_llm_key_features_call_failure_returns_none(monkeypatch):
    monkeypatch.setenv("DIGEST_LLM", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(cl, "_llm_call", lambda *a, **k: None)   # API failed
    assert cl.llm_key_features("X", "<p>body</p>") is None


# --- verified docs-link overrides -------------------------------------------

def test_docs_override_lookup(monkeypatch):
    monkeypatch.setattr(cl, "_overrides_cache", {
        "https://github.blog/changelog/x": "https://docs.github.com/en/foo",
        "https://github.blog/changelog/y": None,
    })
    assert cl.docs_override("https://github.blog/changelog/x") == "https://docs.github.com/en/foo"
    assert cl.docs_override("https://github.blog/changelog/y") is None        # suppress link
    assert cl.docs_override("https://github.blog/changelog/z") is cl._NO_OVERRIDE  # fall through


def test_shipped_overrides_file_is_well_formed():
    import json
    with open(cl._OVERRIDES_FILE) as f:
        data = json.load(f)
    for key, value in data.items():
        if key.startswith("_"):          # documentation comment
            continue
        assert key.startswith("https://github.blog/changelog/"), key
        assert value is None or value.startswith("https://docs.github.com/"), value


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


# --- feature-text normalization & extraction --------------------------------

@pytest.mark.parametrize("raw,expected", [
    # CLI commands keep their lowercase form (not "Gh discussion list").
    ("gh discussion list to scan recent discussions", "gh discussion list to scan recent discussions"),
    ("git push to publish your branch", "git push to publish your branch"),
    # Ordinary lowercase sentences are still capitalized.
    ("improved performance for large repositories", "Improved performance for large repositories"),
    # Slash commands and code tokens are left as-is.
    ("/settings opens a configuration dialog", "/settings opens a configuration dialog"),
])
def test_normalize_feature_text_capitalization(raw, expected):
    assert cl._normalize_feature_text(raw) == expected


def test_key_features_ignores_bold_headings_when_list_items_exist():
    # Bold "Runner type" is a heading, not a feature; a real <li> is present.
    e = _entry("Improvement", title="Copilot code review")
    e.content_html = (
        "<ul><li>Lock the runner setting so org defaults override repo settings</li></ul>"
        "<p><strong>Runner type</strong></p>"
    )
    feats = cl.extract_key_features(e)
    assert any("Lock the runner setting" in f for f in feats)
    assert "Runner type" not in feats


def test_key_features_falls_back_to_bold_when_no_list_items():
    e = _entry("Release", title="Some release")
    e.content_html = "<p><strong>Realtime collaboration mode</strong></p>"
    assert cl.extract_key_features(e) == ["Realtime collaboration mode"]


# --- _condense_feature (recovering over-long list items) ---------------------

def test_condense_feature_cuts_at_first_clause():
    long = ("Organization custom properties are now generally available, giving "
            "enterprise administrators a way to tag organizations with metadata "
            "and apply policies across many repositories at once")
    out = cl._condense_feature(long)
    assert out == "Organization custom properties are now generally available"


def test_condense_feature_strips_dangling_unclosed_paren():
    out = cl._condense_feature(
        "Existing runners below the minimum version required to execute workflow jobs (i.e")
    assert "(" not in out
    assert out.endswith("workflow jobs")


def test_condense_feature_trims_dangling_function_word():
    out = cl._condense_feature(
        "The runner must stay up to date by installing each new release within 30 days of its")
    assert out.endswith("30 days")


def test_condense_feature_leaves_short_text_intact():
    assert cl._condense_feature("Lock the runner setting") == "Lock the runner setting"


def test_key_features_recovers_long_list_item_as_fill():
    long_li = ("Organization custom properties are now generally available, giving "
               "enterprise administrators a scalable way to tag organizations with "
               "metadata and apply governance policies across many repositories at once")
    assert len(long_li) >= 150   # must exceed the old drop threshold
    e = _entry("Release", title="GHES 3.21")
    e.content_html = f"<ul><li>{long_li}</li></ul>"
    feats = cl.extract_key_features(e)
    assert feats == ["Organization custom properties are now generally available"]


def test_key_features_prefers_complete_bullets_over_condensed():
    short = "Lock the runner setting so org defaults override repo settings"
    long_li = ("Set the default Copilot code review runner across every repository in "
               "the organization automatically without any per-repo configuration needed")
    e = _entry("Improvement", title="Copilot code review")
    e.content_html = f"<ul><li>{short}</li><li>{long_li}</li></ul>"
    feats = cl.extract_key_features(e)
    assert feats[0].startswith("Lock the runner setting")   # complete bullet wins


# --- _echoes_title / summary de-duplication ---------------------------------

def test_echoes_title_detects_restatement():
    assert cl._echoes_title("GitHub Agentic Workflows is now in public preview.",
                            "GitHub Agentic Workflows is now in public preview") is True


def test_echoes_title_keeps_substantive_opener():
    assert cl._echoes_title(
        "Two new GitHub-hosted runner images for GitHub Actions are now available for all users.",
        "New runner images in public preview") is False


def test_summary_drops_leading_title_echo():
    e = _entry("Release", title="Feature X is now in public preview")
    e.content_html = ("<p>Feature X is now in public preview. You can now automate "
                      "triage and analysis with it directly.</p>")
    s = cl.extract_detailed_summary(e)
    assert not s.lower().startswith("feature x is now in public preview")
    assert "automate triage" in s


def test_summary_keeps_sole_titleish_sentence():
    e = _entry("Release", title="Feature X is now in public preview")
    e.content_html = "<p>Feature X is now in public preview.</p>"
    # Nothing else to fall back to, so the lone sentence is kept rather than blanked.
    assert "Feature X is now in public preview" in cl.extract_detailed_summary(e)


# --- optional LLM summary (opt-in, graceful fallback) -----------------------

def _fake_anthropic(text=None, raises=None):
    """A stand-in for the `anthropic` package exposing Anthropic().messages.create."""
    mod = types.ModuleType("anthropic")

    class _Msgs:
        def create(self, **kwargs):
            if raises:
                raise raises
            block = types.SimpleNamespace(type="text", text=text)
            return types.SimpleNamespace(content=[block])

    class _Client:
        def __init__(self, *a, **k):
            self.messages = _Msgs()

    mod.Anthropic = _Client
    return mod


def test_llm_summary_disabled_by_default(monkeypatch):
    monkeypatch.delenv("DIGEST_LLM_SUMMARIES", raising=False)
    e = _entry("Release", title="X")
    e.content_html = "<p>Some changelog body text long enough to matter.</p>"
    assert cl.llm_summary(e) is None


def test_llm_summary_requires_api_key(monkeypatch):
    monkeypatch.setenv("DIGEST_LLM_SUMMARIES", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    e = _entry("Release", title="X")
    e.content_html = "<p>Body text.</p>"
    assert cl.llm_summary(e) is None


def test_llm_summary_uses_api_when_enabled(monkeypatch):
    monkeypatch.setenv("DIGEST_LLM_SUMMARIES", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setitem(sys.modules, "anthropic",
                        _fake_anthropic(text="Automates issue triage with coding agents in Actions."))
    e = _entry("Release", title="Agentic workflows")
    e.content_html = "<p>You can automate triage and analysis with coding agents.</p>"
    assert cl.llm_summary(e) == "Automates issue triage with coding agents in Actions."


def test_llm_summary_falls_back_on_api_error(monkeypatch):
    monkeypatch.setenv("DIGEST_LLM_SUMMARIES", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setitem(sys.modules, "anthropic",
                        _fake_anthropic(raises=RuntimeError("429 overloaded")))
    e = _entry("Release", title="X")
    e.content_html = "<p>Body text that is long enough.</p>"
    assert cl.llm_summary(e) is None
