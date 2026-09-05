# First live run

The first time this unit starts a real `claude -p` session. Every test in the
suite injects a fake launcher on purpose, so this is the one step nothing has
stood in for.

Run it **watched**, one trigger at a time. Nothing here is on a schedule
unless you put it there.

---

## Before you start

| | |
|---|---|
| `claude` on PATH | `which claude` |
| a state root you can throw away | anything; `state/projects` is the default |
| **no `MU_SPEC_BUILD_ROOT`** | leave it unset — see *What is fenced off* |

Optional, and worth setting for a first run:

```bash
export MU_SPEC_SESSION_TIMEOUT=600   # 10 min; default is 1800
```

Start the unit:

```bash
python -m mu_spec.main --root state/projects --port 9006
```

`GET /health` should return `{"status": "ok"}`.

---

## 1. Seed the intent

The intent file is yours. The pipeline takes it through the front door like
anything else:

```bash
curl -s -X POST http://127.0.0.1:9006/inbox \
  -H 'Content-Type: application/json' \
  -d '{"type": "initiate", "title": "<one line>", "body": "<the intent file>"}'
```

**Put the whole intent text in `body`.** That is what makes a first run
tractable: triage's cold-start mode is supposed to *interview* whoever asked,
and this unit has no way to talk to a person and must not grow one. A request
that already carries the full brief turns that interview into a summarising
job — `prompts/reference.md` calls these the same skill with opposite
behaviour.

A thin one-line body will not fail; it will produce thin intent and a pile of
flagged assumptions. That is a real result, just not the one to start with.

---

## 2. Dry run — read the prompt before it costs anything

```bash
curl -s -X POST http://127.0.0.1:9006/trigger \
  -H 'Content-Type: application/json' -d '{"dry_run": true}'
```

Selects and renders the exact brief, launches nothing. Check:

- `would_run` is the session type you expected
- `why` matches your reading of the ladder
- `scope` contains everything that session needs and **nothing it should not
  see** — the scope is the guardrail, not the prompt's good manners
- `brief` reads correctly to you

If any of that is wrong, fix it now. A dry run costs nothing and can be
repeated as often as you like.

---

## 3. The real trigger

```bash
curl -s -X POST http://127.0.0.1:9006/trigger -d '{}'
```

One session, then it stops. Advancing again is another trigger.

Watch for:

| Look at | What good looks like |
|---|---|
| the response | `ran: true`, `ok: true`, a plausible `duration_seconds` |
| `gates` | `sound: true`. `complete: false` is normal and is the to-do list |
| `GET /projects/{p}/spine` | entries that actually derive from what they cite |
| `GET /projects/{p}/issues` | assumptions the session flagged — **empty is suspicious**, not clean |
| `GET /projects/{p}/events` | one `session` event per trigger |

The issue queue is the honesty check. A session that derived a whole layer
without flagging one thing it could not derive is more likely to have guessed
silently than to have had perfect inputs.

---

## Aborting

- **Ctrl-C the unit.** The session is a child process and dies with it.
- **A stuck lock** — `state/projects/pipeline.lock` left behind by a killed
  run. Delete it; nothing else reads it.
- **A hung session** is killed at `MU_SPEC_SESSION_TIMEOUT` and reported as a
  failed result, not an exception.

---

## What is fenced off

**Build will not dispatch.** With no `MU_SPEC_BUILD_ROOT` set, the ladder
skips the build rung entirely and reports nothing eligible instead.

This is deliberate and you should leave it that way for now. A build session
runs with `cwd` inside mu-spec's own repository, so one dispatched with
nowhere to write would write the target project's code **into the
specification unit**. Where generated code goes — and how a project names its
own repository — is undesigned.

---

## Known limits, going in

- **No circuit breaker.** Nothing detects a session that exits cleanly having
  achieved nothing. Trigger the same state twice and you get the same
  dispatch twice. Fine while you are watching each one; it is the thing to fix
  before anything runs unattended.
- **No cost or usage cap.** The timeout bounds wall-clock, nothing bounds
  spend.
- **`project_event` shipping is still refused** by the logs unit until that
  vocabulary is updated. `session_run` lands. Neither affects the run.
- **Every session is a fresh process** with no memory of the last one. That is
  the design, not a gap — but it means a session cannot learn from the one
  before it, only from the graph.
