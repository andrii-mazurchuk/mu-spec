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
    one -- so this is tested at the source rather than in the markup."""
    name = _fresh_project(store, prompts)
    for path, key, empty in [
        (f"/projects/{name}/spine", "spine", []),
        (f"/projects/{name}/waves", "waves", []),
        (f"/projects/{name}/issues", "issues", []),
        (f"/projects/{name}/modules", "modules", []),
        (f"/inbox?project={name}", "messages", None),
    ]:
        status, _, raw = handle("GET", path, store, prompts)
        assert status == 200, f"{path} did not answer on an empty project"
        payload = json.loads(raw)
        assert key in payload, f"{path} omitted {key!r} rather than returning it empty"
        if empty is not None:
            assert payload[key] == empty

    status, _, raw = handle("GET", f"/projects/{name}/gates", store, prompts)
    assert status == 200 and json.loads(raw)["sound"] is True

    status, _, raw = handle("GET", f"/projects/{name}/insights", store, prompts)
    assert status == 200
    # No changes yet means no mean, not a zero. A zero would read as a
    # measured score of nought on a page that shows it as one.
    assert json.loads(raw)["change_locality"]["mean"] is None


def test_no_projects_at_all_is_an_empty_list_not_an_error(store, prompts):
    status, _, raw = handle("GET", "/projects", store, prompts)
    assert status == 200
    assert json.loads(raw)["projects"] == []


def test_page_has_an_empty_branch_for_each_region_it_draws():
    """Weaker than the API test above and deliberately kept beside it: this
    one only catches an empty-state branch being deleted, which is the way
    this requirement is actually lost."""
    text = page()
    assert 'id="empty"' in text, "no empty-state element for the graph"
    for marker in ("No projects yet", "No entries in", "No slices yet",
                   "Nothing in the queue", "No issues raised"):
        assert marker in text, f"no empty state for {marker!r}"
    # A null mean must not be printed as a number.
    assert "locality==null" in text.replace(" ", "")


def test_page_carries_the_ratification_decision():
    """A pending partition is a decision waiting on a human, so it lives in
    Queues beside the other two -- no page and no rail item of its own."""
    text = page()
    for marker in ("Awaiting your ratification", "do-ratify", "do-reject",
                   "slicing/proposal", "Preview in the spine"):
        assert marker in text, f"the decision surface lost {marker!r}"


def test_the_deciding_controls_write_through_declared_actions():
    """The one write this page makes, and it is an ordinary relative POST.

    Served at /dashboard it reaches this unit directly; framed under the
    node it lands inside the proxied prefix, where the proxy forwards it
    because the path is one of the two declared at /actions. No
    postMessage, no node internals, still works standalone.
    """
    text = page()
    assert 'method:"POST"' in text.replace(" ", ""), "the page makes no write at all"
    # Relative, like every read. An absolute path would reach the NODE.
    assert not re.search(r"fetch\(\s*[\"'`]\s*/", text)
    assert '"projects/"+enc(STATE.project)+"/slicing/proposal/"' in text.replace(" ", "")
    for control in ("do-ratify", "do-reject-confirm"):
        assert f'"{control}").onclick' in text, f"{control} does nothing"


def test_a_rejection_cannot_be_submitted_without_a_reason():
    """The note is not decoration: it is kept as `last_rejection` and shown
    to the next slicing session. Without it that session proposes the same
    cut again and the round is wasted."""
    text = page()
    assert 'id="do-reject-confirm" disabled' in text, "confirm starts enabled"
    assert '"reject-note").oninput' in text, "nothing ever enables it"


def test_the_notice_does_not_claim_the_write_is_enforced():
    """Absent from /tools means *not offered to the model*, and not one
    step more. A caller with a shell reaches this unit directly whatever
    the dashboard does. Saying otherwise on the page would be the page
    claiming a guarantee the system does not make."""
    text = page()
    assert "not one step more" in text or "not one step further" in text
    assert "do not read this as enforced" in text.lower()


def test_preview_does_not_overwrite_real_slice_membership():
    """Previewing a decision must never look like having taken it, so the
    proposed membership is held beside the real one rather than replacing
    it, and the strip carries a flag while it is on."""
    text = page()
    assert "trueSlice" in text and "propSlice" in text
    assert "previewflag" in text


def test_page_carries_both_graph_views():
    """Spine answers what derives from what. Order answers what waits on
    what -- the question the spine answers badly, because depends_on is
    same-layer and lands as one arc among a hundred."""
    text = page()
    for marker in ('data-v="order"', "drawOrder", "buildOrderScene", "stepDepth",
                   "drawScene"):
        assert marker in text, f"the order view lost {marker!r}"


def test_the_order_view_cannot_hang_on_a_dependency_cycle():
    """The gates refuse a cycle, but this view has to survive drawing a
    graph that has not passed them yet."""
    text = page()
    assert "seen.has(id)) return 0" in text.replace("  ", " ") or            "if(seen.has(id)) return 0" in text


def test_grouped_columns_share_a_midline():
    """Layers are wildly uneven -- nine intents against thirty-four
    architecture entries -- and hanging every column from the top puts the
    short one opposite the start of the long one."""
    text = page()
    assert "assignYGrouped" in text
    assert "tallest" in text and "t-group" in text


def test_the_order_view_does_not_write_into_the_crossings_kpi():
    """Cross-slice dependencies and edge crossings are different numbers.
    One under the other's label is how a dashboard starts lying quietly."""
    text = page()
    assert "crossSlice" in text, "the cross-slice count lost its own name"


def test_page_escapes_stored_text():
    """Entry bodies, titles and issue claims were written by whoever
    could reach this unit. They are shown as text, never parsed as
    markup."""
    text = page()
    assert "function esc" in text or "const esc=" in text
    # No identifier interpolated straight into an inline handler.
    assert not re.search(r'onclick="\w+\(\$\{', text)
