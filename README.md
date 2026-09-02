# mu-docs

A memory unit in a holonic-node harness. It owns **authoritative per-project
documentation**: the structured description of what each project is and what
it should do.

Documentation here is the source work gets driven from, not a record written
after the fact. Changing a project's documentation is the intended way to
express that the project should change.

## The boundary

**mu-docs stores and serves. It never executes.**

It does not launch sessions, run code, modify any project's source, or decide
when work should happen. What it does is make documentation change legible —
what the docs say now, what changed, and what that implies is wanted. The
processing unit reads that and does the work.

That split is what lets either side be replaced. Collapsing them would
duplicate the processing unit's gate, session handling, and task access here.

Among the memory units: one holds best-effort keyword-matched agent memory
(optional, lossy by design), another holds append-only session and judgment
logs (required infrastructure). mu-docs is the third — versioned, editable,
authoritative project documentation. Different durability guarantees,
different consumers, hence its own unit.

## Status

Scaffolding only. The four standard unit endpoints are implemented and
tested; `/tools` is deliberately empty because no capability exists yet. The
document store, its schema, and the change-detection surface are the next
piece of work and are not designed yet.

## Commands

```bash
pip install -e ".[dev]"
pytest
pytest tests/test_server.py::test_health_returns_ok   # single test

python -m mu_docs.main                                # defaults to :9006
python -m mu_docs.main --port 9106                    # override
```

Configuration comes from the environment the gateway injects at process
start: `MU_DOCS_HOST`, `MU_DOCS_PORT`, `MU_DOCS_PROMPTS_DIR`.

## Registering it

Add to the gateway's `units.yaml`:

```yaml
  - name: mu-docs
    source:
      git_url: https://github.com/lainiwakuraagent-lgtm/mu-docs.git
      ref: main
    local_path: units/mu-docs
    lifecycle: persistent
    base_url: http://127.0.0.1:9006
    unit_type: memory
    start_cmd: ["python3", "-m", "mu_docs.main"]
    prompts:
      - path: prompts/default.md
        tier: default
        consumers: []
```

`capabilities:` stays empty until real endpoints exist; `consumers:` gets
filled in once it's decided which units should learn about mu-docs when
assembling their own context.

## Development

See `CLAUDE.md`. It is self-contained on purpose: everything needed to work
in this repo, including the system standard this unit implements, is written
there. **This repo is developed in isolation — do not read other units'
source.**
