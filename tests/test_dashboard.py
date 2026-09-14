"""The dashboard page, and the rules the standard puts on a page-tier unit.

The rule worth the most here is the last one: the page must be present
*as installed*, not merely present in the checkout. A test that opens
`mu_spec/dashboard.html` by path passes whether or not the packaging
ships the file, so it proves nothing about the thing that actually
breaks -- a wheel without the asset, a 404, and a node that reports the
unit as having no dashboard at all.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from mu_spec import dashboard
from mu_spec.server import handle
from mu_spec.storage import ProjectStore


@pytest.fixture()
def store(tmp_path):
    return ProjectStore(tmp_path / "projects")


@pytest.fixture()
def prompts(tmp_path):
    d = tmp_path / "prompts"
    d.mkdir()
    (d / "default.md").write_text("# mu-spec\n", encoding="utf-8")
    return d


def test_page_is_readable_as_installed():
    """Resolved through importlib.resources -- the installed location,
    whatever it turns out to be -- never through a checkout path."""
    page = dashboard.read_page()
    assert page is not None, "dashboard.html did not survive packaging"
    assert page.lstrip().lower().startswith("<!doctype html>")


def test_dashboard_is_served_as_html(store, prompts):
    status, content_type, body = handle("GET", "/dashboard", store, prompts)
    assert status == 200
    assert content_type.startswith("text/html")
    assert "<title>" in body


def test_dashboard_refuses_other_methods(store, prompts):
    status, _, _ = handle("POST", "/dashboard", store, prompts)
    assert status == 405


def test_dashboard_is_not_declared_as_a_tool(store, prompts):
    """It is for a human in a browser. A model offered it would fetch a
    page of HTML in place of the data behind it."""
    _, _, raw = handle("GET", "/tools", store, prompts)
    names = {t["name"] for t in json.loads(raw)["tools"]}
    assert not any("dashboard" in n for n in names)


# -- the standard's rules for a page-tier unit ---------------------------


def page() -> str:
    text = dashboard.read_page()
    assert text is not None
    return text


def test_page_is_self_contained():
    """One file, inline CSS and JS. No CDN, no external stylesheet, no
    build step -- so nothing has to be reachable for it to render."""
    text = page()
    assert "<link" not in text.lower()
    assert not re.search(r'<script[^>]+\bsrc=', text, re.I)
    assert not re.search(r'https?://(?!www\.w3\.org)', text)


def test_every_fetch_is_relative():
    """Served at /dashboard here and at /dashboard/mu-spec/ through the
    node's proxy. A relative path lands correctly in both; an absolute
    one reaches the NODE, which answers with plausible JSON of the wrong
    shape rather than an error."""
    text = page()
    bad = re.findall(r'fetch\(\s*["\'`]\s*/', text)
    assert not bad, "an absolute fetch path escapes the proxied prefix"
    assert re.search(r'fetch\(\s*\w', text), "the page fetches nothing at all"


def test_page_opts_into_the_node_theme():
    """Without this meta the node injects nothing and the page stays
    pinned to whichever palette its author was looking at."""
    assert 'name="holonic-tokens"' in page()


def test_page_colours_come_from_contract_tokens():
    text = page()
    for token in ("--ground", "--panel", "--ink", "--rule", "--brand", "--dim"):
        assert token in text, f"{token} is not defined as a standalone default"
    # --muted is a shadcn *surface* in the console; using it for text
    # paints text the same colour as the panel behind it.
    assert "var(--muted)" not in text


def test_page_draws_no_navigation_of_its_own():
    """The topbar and the unit switcher belong to the node and never
    unmount. What this page has is in-page scope, not navigation."""
    text = page()
    assert "<header" not in text.lower()
    assert not re.search(r'<a\s[^>]*href="https?://', text, re.I)


def test_page_escapes_stored_text():
    """Entry bodies, titles and issue claims were written by whoever
    could reach this unit. They are shown as text, never parsed as
    markup."""
    text = page()
    assert "function esc" in text or "const esc=" in text
    # No identifier interpolated straight into an inline handler.
    assert not re.search(r'onclick="\w+\(\$\{', text)
