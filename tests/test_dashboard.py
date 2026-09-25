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
    assert '"do-reject-confirm"${' in text.replace(" ", ""), (
        "confirm is no longer driven by whether a reason exists"
    )
    assert "rejectDraft.note.trim()?" in text.replace(" ", "")
    assert '"reject-note").oninput' in text, "nothing ever records the reason"


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
    assert "tallest" in text


def test_slice_grouping_is_the_only_layout():
    """Ungrouped was a second reading of the same picture that nobody chose
    deliberately, and the control mostly sat on the wrong setting."""
    text = page()
    assert "const GROUPED=true;" in text
    assert "t-group" not in text, "the toggle should be gone, not defaulted"


def test_grouping_resolves_a_slice_through_every_node_being_drawn():
    """Slices were read off STATE.byId, which holds no ghosts -- so every
    copy fell into "unsliced" and the band lost slice grouping exactly where
    the reader needs to know which column a scenario judges."""
    text = page()
    assert "const nodeOf = id =>" in text
    assert 'const s=nodeOf(id).slice||"unsliced";' in text


def test_a_copy_can_actually_be_clicked():
    """An SVG rect with fill:none takes no pointer events, so a click on a
    ghost fell through to the canvas -- which reads that as clicking blank
    space and CLEARS the selection. Inert and quietly destructive."""
    text = page()
    ghost = re.search(r"\.node\.ghost rect\{([^}]*)\}", text).group(1)
    unbuilt = re.search(r"\.node\.unbuilt rect\{([^}]*)\}", text).group(1)
    for rule in (ghost, unbuilt):
        assert "fill:none" in rule
        assert "pointer-events:all" in rule


def test_the_fork_can_be_drawn_two_ways():
    """Band and inline trade against each other rather than one being right:
    band reads as one picture but duplicates every judged contract and puts
    the chain back to intent off screen; inline duplicates nothing and reads
    straight across but scatters verification down the whole corpus."""
    text = page()
    assert 'let LAYOUT="band";' in text
    assert 'const inline = LAYOUT === "inline";' in text
    # the data is identical in both -- this is a drawing decision only
    assert "const corpus = inline ? STATE.entries" in text


def test_the_order_view_does_not_write_into_the_crossings_kpi():
    """Cross-slice dependencies and edge crossings are different numbers.
    One under the other's label is how a dashboard starts lying quietly."""
    text = page()
    assert "crossSlice" in text, "the cross-slice count lost its own name"


def test_page_carries_a_reading_view():
    """The graph views answer questions about structure. Neither answers
    "what does this project actually say", which is what a person needs
    while the entries are still being written and each has to be read at
    least once. Clicking 78 nodes is auditing, not reading."""
    text = page()
    for marker in ('data-v="read"', "drawRead", "READ_CACHE", "rd-layer", "rd-find"):
        assert marker in text, f"the read view lost {marker!r}"


def test_the_reading_view_reads_whole_layers_not_single_entries():
    """One request per layer, bodies included, from the endpoint a reviewer
    already uses. Per-entry fetches would be 78 round trips to render one
    document."""
    text = page()
    assert "/review?layer=" in text
    # The per-entry read stays for the inspector, and must not be what the
    # document is built from.
    assert text.count("/review?layer=") >= 1


def test_the_reading_view_survives_two_loads_in_flight():
    """Switching layer twice quickly leaves two requests running. The slower
    one must not overwrite the faster one's document."""
    text = page()
    assert "READ_TOKEN" in text
    assert "mine!==READ_TOKEN" in text.replace(" ", "")


def test_bodies_are_escaped_in_the_reading_view():
    """This view renders more agent-written text than anything else on the
    page -- every body at once. Requirement 8 applies hardest here."""
    text = page()
    assert 'class="bd">${esc(r.body' in text.replace(" ", "") or            '${esc(r.body||"")}' in text


# -- staying current ----------------------------------------------------


def test_the_page_refreshes_itself():
    """The node cannot do this for a page-tier unit: its only lever is
    reloading the frame, which discards pan, zoom, selection, the outline's
    position and any half-typed text."""
    text = page()
    for marker in ("POLL_MS", "function tick", "schedulePoll", "startPolling"):
        assert marker in text, f"the poller lost {marker!r}"
    assert "10000" in text, "the interval no longer matches the node's chrome"


def test_an_unchanged_poll_touches_nothing():
    """Rule 1, and the one that does the most work. Most polls find no
    change, so the common case must cost a string comparison and no DOM."""
    text = page()
    assert "LAST_SIG" in text and "function signatures" in text
    assert "every(k => now[k] === was[k])" in text, "nothing short-circuits"


def test_a_poll_never_steals_the_view():
    """Rule 2. A redraw driven by a poll must not re-fit the graph: pan and
    zoom are state the reader created."""
    text = page()
    assert "KEEP_VIEW" in text
    assert text.count("if(!KEEP_VIEW) requestAnimationFrame(fit)") >= 2, (
        "a view redraw path re-fits during a poll"
    )


def test_a_structural_redraw_puts_the_selection_back():
    """drawScene rebuilds every node, discarding the classes that carry the
    selection and the slice focus. Without a repaint the graph silently
    un-dims itself on the tick that adds an entry."""
    text = page()
    i = text.index("if(structural && MODE !== \"read\")")
    assert "repaint();" in text[i:i + 500], "a structural redraw drops the selection"


def test_only_transient_interaction_pauses_the_refresh():
    """Rule 4's first half. A drag, an open menu and a click in flight each
    resolve in seconds on their own, so pausing for them is safe."""
    text = page()
    assert "function readerIsBusy" in text
    for guard in ("POINTER_DOWN", "dragging", "SELECT"):
        assert guard in text, f"readerIsBusy ignores {guard}"


def test_uncommitted_input_is_protected_without_freezing_the_page():
    """Rule 4's second half, and the trap it names. Pausing on a half-written
    note looks like the same rule: a reader who types one character and
    wanders off would stop the page updating indefinitely, with nothing on
    screen to explain it.

    Protection means *this field is not yours to overwrite* -- so the draft
    lives in STATE and the textarea is rendered from it. Reconciliation
    cannot clobber what it does not author.
    """
    text = page()
    assert "rejectDraft" in text, "the draft is not held anywhere durable"
    # The note must NOT be a reason to stop polling.
    start = text.index("function readerIsBusy")
    body = text[start:start + 700]
    assert "reject-note" not in body, "a half-written note still freezes the page"
    assert "RD_FIND" not in body, "a typed filter still freezes the page"
    # Ends on submit or cancel, never on blur.
    assert text.count('rejectDraft={open:false,note:""}') >= 2, (
        "the draft is not cleared on both submit and cancel"
    )
    assert "onblur" not in text.lower()


def test_the_caret_survives_a_refresh_too():
    """Rendering from STATE preserves the text but not where the caret was,
    and a refresh that drops it mid-sentence is the same theft in a smaller
    form."""
    text = page()
    assert "captureDraftFocus" in text and "restoreDraftFocus" in text
    assert "setSelectionRange" in text


def test_a_hidden_tab_does_not_poll():
    """Rule 5. A frame inherits the parent page's visibility, so this needs
    nothing from the node."""
    assert "visibilityState" in page()


def test_failure_backs_off_and_is_visible():
    """Rule 6. A dashboard that silently keeps showing its last good data
    while the unit is unreachable is lying."""
    text = page()
    assert "BACKOFF_MAX" in text
    assert "unreachable" in text


def test_the_reading_position_is_restored_instantly():
    """Restoring a position the reader never left must not animate, or the
    document slides under their eyes on every tick. Smooth scrolling is
    opted into per gesture instead of set on the container."""
    text = page()
    assert "scroll-behavior:smooth}" not in text.replace(" ", ""), (
        "a container-level smooth scroll makes restores non-deterministic"
    )
    assert "drawReadKeepingPlace" in text
    assert 'behavior:"smooth"' in text.replace(" ", ""), "no gesture opts in"


def test_page_escapes_stored_text():
    """Entry bodies, titles and issue claims were written by whoever
    could reach this unit. They are shown as text, never parsed as
    markup."""
    text = page()
    assert "function esc" in text or "const esc=" in text
    # No identifier interpolated straight into an inline handler.
    assert not re.search(r'onclick="\w+\(\$\{', text)


# -- the verification band ---------------------------------------------------


def test_tests_are_a_column_between_spec_and_modules():
    """The column order is load-bearing. Tests sit between Spec and Modules on
    x, so the corpus leaves that column empty and the band below occupies
    exactly Spec, Tests and Modules -- which is what puts a scenario under the
    contract it judges rather than beside it."""
    page = dashboard.read_page()
    layers = re.search(r"const LAYERS=\[(.*?)\];", page, re.S).group(1)
    order = re.findall(r'\["([IBASTM])"', layers)
    assert order == ["I", "B", "A", "S", "T", "M"]


def test_the_band_is_laid_out_separately_from_the_corpus():
    """Two bands, laid out independently. Crossing reduction inside the
    verification band must not be dragged around by the corpus above it, so
    adjacency is built from the node set being laid out rather than from all
    of STATE."""
    page = dashboard.read_page()
    assert "function layoutBand(" in page
    assert "function adjacency(nodes)" in page
    assert "STATE.entries.filter(e => e.band !== VERIFY)" in page


def test_a_ghost_is_never_an_entry():
    """The copy of a spec entry under the scenarios that judge it exists so
    that edge is one short hop instead of an arc across the whole picture.
    It must resolve back to the real entry everywhere a reader can act on
    it, or selecting one would open something that does not exist."""
    page = dashboard.read_page()
    assert "const GHOST = id =>" in page
    assert "const realOf = id =>" in page
    # selection resolves the copy to the entry it copies
    assert re.search(r"function pick\(id\)\{\s*\n\s*id = realOf\(id\);", page)


def test_scenarios_have_no_slice_of_their_own():
    """A test's column is its parent's, resolved from the graph. A stored
    membership would be the second copy that goes stale the first time a
    slice splits -- which is the same argument the unit itself makes."""
    page = dashboard.read_page()
    assert "if(e.test) e.slice = (byId[e.derives[0]] || {}).slice" in page


def test_an_unbuilt_scenario_is_not_styled_as_a_failure():
    """A scenario written but not built is in no work unit, orders nothing,
    and is not yet expected to pass. Drawn as unfinished, never as broken --
    so it must not take a warning colour."""
    page = dashboard.read_page()
    rule = re.search(r"\.node\.unbuilt rect\{([^}]*)\}", page).group(1)
    assert "--warn" not in rule and "--down" not in rule
    assert "stroke-dasharray" in rule


def test_work_units_on_the_spine_are_shown_on_selection_not_drawn():
    """A unit groups entries and files across columns AND across both bands,
    so a hull or a column around one states an adjacency the graph does not
    have. Selection lights the peers instead."""
    page = dashboard.read_page()
    assert "function unitSet()" in page
    assert '.node.peer rect{' in page
    assert "<hull" not in page


def test_the_spine_reads_the_live_projection_not_the_stored_cut():
    """The two answer different questions. The spine answers "what would I
    build now", so it takes the live graph -- the same choice get_work_unit
    makes. A stale cut made an entry's Verification panel say it follows a
    test unit directly above a Work unit panel saying it waits on nothing."""
    page = dashboard.read_page()
    body = re.search(r"function unitSource\(\)\{(.*?)\n\}", page, re.S).group(1)
    assert "u.live" in body
    assert body.index("u.live") < body.index("u.cut")


def test_the_waves_copy_states_both_sources_of_unit_order():
    """It used to say order comes from depends_on and nothing else. That was
    true until verification became a second source, and a paragraph
    confidently stating a false rule is worse than no paragraph."""
    page = dashboard.read_page()
    assert "Order has</b> two sources" in page or "Order has <b>two</b> sources" in page
    assert "follows the unit\n    holding the scenarios" in page \
        or "follows the unit" in page
