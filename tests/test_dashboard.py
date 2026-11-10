"""The dashboard page, and the rules the standard puts on a page-tier unit.

The rule worth the most here is the first one: the page must be present
*as installed*, not merely present in the checkout. A test that opens
`mu_spec/dashboard.html` by path passes whether or not the packaging
ships the file, so it proves nothing about the thing that actually
breaks -- a wheel without the asset, a 404, and a node that reports the
unit as having no dashboard at all.

The page is built from `app/` into one self-contained file (UNIT_CONTRACT.md,
"The dashboard"); CI checks the committed file matches a fresh build. Two
kinds of check follow, and each says which it is:

- BUILT: reads the installed page through `importlib.resources`. Anything a
  browser would act on -- tags, URLs, fetch paths, tokens, visible strings --
  survives minification, so it is checked where it ships.
- SOURCE: reads `app/src`. Used only where minification makes the built file
  unreadable for the question: which identifiers the page writes into the DOM
  (React's own runtime names `innerHTML` and `dangerouslySetInnerHTML`, so the
  built file always contains them), which paths it POSTs to, and how a control
  derives its state.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from mu_spec import dashboard
from mu_spec.server import handle
from mu_spec.storage import ProjectStore

APP = Path(__file__).resolve().parent.parent / "app"
SRC = APP / "src"


@pytest.fixture()
def store(tmp_path):
    return ProjectStore(tmp_path / "projects")


@pytest.fixture()
def prompts(tmp_path):
    d = tmp_path / "prompts"
    d.mkdir()
    (d / "default.md").write_text("# mu-spec\n", encoding="utf-8")
    return d


def page() -> str:
    """BUILT: the page as installed."""
    text = dashboard.read_page()
    assert text is not None
    return text


def source() -> str:
    """SOURCE: every shipped TypeScript file, tests excluded."""
    files = sorted(p for p in SRC.rglob("*.ts*") if not p.name.endswith(".test.ts"))
    assert files, "app/src is missing"
    return "\n".join(p.read_text(encoding="utf-8") for p in files)


def test_page_is_readable_as_installed():
    """Resolved through importlib.resources -- the installed location,
    whatever it turns out to be -- never through a checkout path."""
    text = dashboard.read_page()
    assert text is not None, "dashboard.html did not survive packaging"
    assert text.lstrip().lower().startswith("<!doctype html>")


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


def test_page_is_self_contained():
    """BUILT. One file, CSS, JS and fonts inline. No CDN, no external
    stylesheet, no favicon link -- nothing has to be reachable for it to
    render. The build step is allowed; the committed output is what ships."""
    text = page()
    assert "<link" not in text.lower()
    assert not re.search(r'<script[^>]+\bsrc=', text, re.I)
    assert not re.search(r'https?://(?!www\.w3\.org)', text)
    # url() may only carry inlined data, never a file next to the page.
    assert not re.search(r'url\((?!["\']?data:)', text)


def test_every_fetch_is_relative():
    """Served at /dashboard here and at /dashboard/mu-spec/ through the
    node's proxy. A relative path lands correctly in both; an absolute
    one reaches the NODE, which answers with plausible JSON of the wrong
    shape rather than an error.

    BUILT for the fetch calls themselves; SOURCE for the paths, which the
    minifier moves away from the call."""
    text = page()
    assert not re.findall(r'fetch\(\s*["\'`]\s*/', text), "an absolute fetch path escapes the proxied prefix"
    assert re.search(r'fetch\(\s*\w', text), "the page fetches nothing at all"
    src = source()
    paths = re.findall(r'\b(?:get|post|get<[^>]*>)\(\s*[`"\']([^`"\']*)', src)
    assert paths, "no read or write paths found in app/src"
    assert not [p for p in paths if p.startswith("/")], "a path in app/src starts with a slash"


def test_page_opts_into_the_node_theme():
    """Without this meta the node injects nothing and the page stays
    pinned to whichever palette its author was looking at."""
    assert 'name="holonic-tokens"' in page()


def test_page_colours_come_from_contract_tokens():
    """BUILT. Each surface reads a contract token, with the page's own value
    as the standalone fallback, so the node's palette wins when framed."""
    text = page()
    for token in ("--ground", "--panel", "--ink", "--rule", "--brand", "--dim"):
        assert f"var({token}," in text, f"{token} is not read with a standalone default"
    # --muted is a shadcn *surface* in the console; using it for text
    # paints text the same colour as the panel behind it.
    assert "var(--muted)" not in text


def test_page_draws_no_navigation_of_its_own():
    """The topbar and the unit switcher belong to the node and never
    unmount. What this page has is in-page scope, not navigation: its links
    are hash routes inside the page."""
    text = page()
    assert "<header" not in text.lower()
    assert not re.search(r'<a\s[^>]*href="https?://', text, re.I)
    src = source()
    # SOURCE: every href the page renders is a hash route.
    hrefs = re.findall(r'href=\{?[`"]([^`"]*)', src)
    assert hrefs and all(h.startswith("#/") for h in hrefs), [h for h in hrefs if not h.startswith("#/")]
    assert "location.href" not in src and "window.open" not in src and "location.assign" not in src


# -- requirement 8: stored text never becomes markup -----------------------


def test_stored_text_never_becomes_markup():
    """SOURCE. Entry bodies, titles, issue claims and tracker reasons arrive
    through doors open by design. React renders them as text; the only way to
    turn one into markup is one of these sinks, and the page uses none. The
    built file cannot answer this: React's runtime names innerHTML itself."""
    src = source()
    for sink in ("dangerouslySetInnerHTML", "innerHTML", "outerHTML", "insertAdjacentHTML",
                 "document.write", "eval(", "new Function", "srcdoc", "javascript:"):
        assert sink not in src, f"app/src uses {sink}"
    # BUILT: no inline handler interpolated from data either.
    assert not re.search(r'onclick="', page(), re.I)


def test_selected_state_is_derived_from_current_state():
    """SOURCE. aria-pressed, aria-current and the selected row are computed
    from what is selected now on every render, never written once at mount --
    a control written at build time announces the mount-time selection
    forever. So no such attribute is ever a literal."""
    src = source()
    assert not re.search(r'aria-(?:pressed|current|selected)=(?:"|\{\s*(?:true|false)\s*\})', src)
    assert re.search(r'aria-pressed=\{[^}]*===', src), "no control derives aria-pressed from a comparison"


# -- requirement 5: degrades on empty ----------------------------------
#
# The half that rots silently. Once a store has data in it nobody opens
# the empty case again, and the failure is invisible until the day a new
# project is created -- which is exactly when somebody is watching.


def _fresh_project(store, prompts, name="fresh"):
    """A project that exists and holds nothing, built the way the pipeline
    builds one: a request, then a project citing it."""
    _, _, raw = handle(
        "POST", "/inbox", store, prompts,
        {"type": "initiate", "title": "t", "project": name},
    )
    mid = json.loads(raw)["message_id"]
    handle("POST", "/projects", store, prompts,
           {"project": name, "in_response_to": mid})
    return name


def test_every_endpoint_the_page_reads_answers_on_an_empty_project(store, prompts):
    """The page degrades only if the API does. A 500 or a missing key on a
    project with nothing in it renders as a broken dashboard, not an empty
    one -- so this is tested at the source rather than in the markup. The
    list is every read in app/src/lib/api.ts."""
    name = _fresh_project(store, prompts)
    for path, key, empty in [
        (f"/projects/{name}/spine", "spine", []),
        (f"/projects/{name}/waves", "waves", []),
        (f"/projects/{name}/issues", "issues", []),
        (f"/projects/{name}/modules", "modules", []),
        (f"/projects/{name}/units/cuts", "cuts", []),
        (f"/projects/{name}/labels", "labels", []),
        (f"/projects/{name}/repo", "repo", None),
        (f"/projects/{name}/units", "cut", None),
        (f"/projects/{name}/emit", "running", None),
        (f"/projects/{name}/delegation", "delegation", None),
        (f"/projects/{name}/slicing/proposal", "status", None),
        (f"/projects/{name}/insights", "slices", None),
        (f"/projects/{name}/events?kind=tickets_emitted", "events", []),
        (f"/projects/{name}/events?kind=ship_failed", "events", []),
        (f"/projects/{name}/review?layer=B", "entries", []),
        (f"/inbox?project={name}", "messages", None),
    ]:
        status, _, raw = handle("GET", path, store, prompts)
        assert status == 200, f"{path} did not answer on an empty project"
        payload = json.loads(raw)
        assert key in payload, f"{path} omitted {key!r} rather than returning it empty"
        if empty is not None:
            assert payload[key] == empty, path

    status, _, raw = handle("GET", f"/projects/{name}/gates", store, prompts)
    assert status == 200 and json.loads(raw)["sound"] is True


def test_no_projects_at_all_is_an_empty_list_not_an_error(store, prompts):
    status, _, raw = handle("GET", "/projects", store, prompts)
    assert status == 200
    assert json.loads(raw)["projects"] == []


def test_page_has_an_empty_branch_for_each_region_it_draws():
    """BUILT. Weaker than the API test above and deliberately kept beside it:
    this one only catches an empty-state branch being deleted, which is the
    way this requirement is actually lost. Visible strings survive minifying."""
    text = page()
    for marker in ("No projects yet", "The spec has no entries yet", "No slices yet",
                   "No work units yet", "No entries in the", "No files declared yet",
                   "No pending requests", "No open issues", "No cut yet", "No ships yet"):
        assert marker in text, f"no empty state for {marker!r}"


def test_view_state_comes_from_the_reads_not_the_url():
    """SOURCE. The design harness rendered states from ?state=; the product
    takes them from what the unit answered, or a broken unit would render as
    whatever the URL said."""
    src = (SRC / "lib" / "screen-state.ts").read_text(encoding="utf-8")
    assert "?state" not in src and "URLSearchParams" not in src
    assert "useStore" in src


# -- the writes ------------------------------------------------------------


def test_every_write_goes_through_the_one_helper():
    """SOURCE. One POST in the page, in lib/api.ts, and it names the
    dashboard as the actor -- so the actor and the declared-path checks in
    tests/test_server.py cover every write by construction."""
    src = source()
    assert len(re.findall(r'method:\s*"POST"', src)) == 1
    api = (SRC / "lib" / "api.ts").read_text(encoding="utf-8")
    assert 'method: "POST"' in api
    assert 'actor: "dashboard"' in api


def test_page_carries_the_ratification_decision():
    """A pending partition is a decision waiting on a human, so it lives in
    Queues beside the other two -- no page of its own."""
    assert "A slicing proposal is waiting on you" in page()
    api = (SRC / "lib" / "api.ts").read_text(encoding="utf-8")
    assert "/slicing/proposal/ratify`" in api and "/slicing/proposal/reject`" in api


def test_a_rejection_cannot_be_submitted_without_a_reason():
    """SOURCE. The note is not decoration: it is kept as `last_rejection` and
    shown to the next slicing session. Without it that session proposes the
    same cut again and the round is wasted."""
    src = (SRC / "pages" / "screens.tsx").read_text(encoding="utf-8")
    assert "disabled={!note.trim()" in src
    assert "write.reject(p, note.trim())" in src


def test_the_token_is_a_verdict_and_never_a_value():
    """The unit reports a boolean; nothing on the page asks for, renders, or
    stores the token, and there is no route that would return one. The only
    mention is the variable NAME, as a remedy for a missing token."""
    text, src = page(), source()
    assert 'id="token' not in text and "token-in" not in text
    assert src.count("MU_SPEC_GITHUB_TOKEN") == 1
    assert "d.emission.token" in src


def test_the_spine_draws_only_edges_the_unit_has():
    """mu-spec stores derives-from and depends-on. The design also drew
    "contradicts" and "parent of"; drawing either would be faking data."""
    code = re.sub(r"//[^\n]*", "", source())  # the comment saying why may name it
    for text in (page(), code):
        assert "contradicts" not in text
    assert re.search(r'export type EdgeKind = "derives" \| "depends"\n', source())
