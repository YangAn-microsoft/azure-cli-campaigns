# Adding a new campaign

This guide walks through writing a campaign from scratch. A *campaign*
is a class that, given some params, produces a `CampaignPlan` —
basically the title, intro, and list of `Item`s for a tracking issue.
The framework handles the rest: creating/updating the issue, dispatching
handlers, persisting state.

If you just want to dispatch an existing campaign, see the
top-level [README](../README.md#running-a-campaign).

---

## 1. Decide what your campaign does

A campaign is the right abstraction when **all** of these hold:

- The work spans **multiple repos** or **multiple PRs in one repo**.
- The work needs **tracking** — you want a single issue that shows
  progress across all the pieces.
- The work is **repeatable** — you'll do this kind of rollout again
  (e.g. every Python minor, every Azure CLI release, every CVE batch).

One-shot scripts are not campaigns. Use a campaign when you want a
human-readable tracker that reflects real-world progress.

---

## 2. Anatomy of a campaign

```
campaigns/
└── my_campaign/                 # one directory per campaign
    ├── __init__.py              # Campaign class + handlers (entry point)
    ├── intro.md                 # tracking-issue intro template (optional)
    └── my_handler/              # one subdir per non-trivial handler
        ├── __init__.py
        ├── agent.py             # handler logic (a function returning HandlerResult)
        └── tests/
```

The four objects you'll touch live in [`campaigns/base.py`](../campaigns/base.py):

| Object | Role |
|---|---|
| `Item` | One checklist row. Has `id`, `title`, optional `handler`, `params`, `repo`, `phases`, `depends_on`. |
| `CampaignPlan` | What `Campaign.build()` returns: `title`, `intro`, `items`, `placeholders`. |
| `HandlerContext` | What handlers receive: `item`, `repo`, `issue_number`, `params`, `force_recreate`, `dry_run`. |
| `HandlerResult` | What handlers return: `status`, `pr` (optional), `notes`, `phase`. |

---

## 3. Minimal "hello world" campaign

A campaign with one manual item and no handler.

```python
# campaigns/hello_campaign/__init__.py
from ..base import Campaign, CampaignPlan, Item


class HelloCampaign:
    id = "hello"

    def __init__(self, issue_repo: str = "YourOrg/playground"):
        self.issue_repo = issue_repo

    def build(self, params: dict) -> CampaignPlan | None:
        target = params.get("target", "world")
        return CampaignPlan(
            title=f"Say hello to {target}",
            intro=f"Demo campaign greeting **{target}**.",
            items=[
                Item(id="greet", title=f"Wave at {target}"),
            ],
            placeholders={"target": target},
        )
```

Register it in [`campaigns/registry.py`](../campaigns/registry.py):

```python
from .hello_campaign import HelloCampaign

CAMPAIGNS: dict[str, Campaign] = {
    "python-upgrade": PythonUpgradeCampaign(),
    "hello": HelloCampaign(),
}
```

Add the id to the workflow input list in
[`.github/workflows/RunCampaign.yml`](../.github/workflows/RunCampaign.yml):

```yaml
campaign:
  type: choice
  options:
    - python-upgrade
    - hello
```

Dry-run it locally:

```pwsh
python -m run_campaign --campaign hello --dry-run `
    --params '{"target":"reviewers"}' --issue-repo YourOrg/playground
```

You should see the planned issue title and the single item printed.

---

## 4. Adding a handler

A handler is a function `(HandlerContext) -> HandlerResult`. It's what
*does* the work — opens a PR, runs a check, hits an API, whatever.

```python
# campaigns/hello_campaign/__init__.py
from ..base import (
    Campaign, CampaignPlan, HandlerContext, HandlerResult, Item,
)


def hello_handler(ctx: HandlerContext) -> HandlerResult:
    target = ctx.params.get("target", "world")
    if ctx.dry_run:
        return HandlerResult(status="completed",
                             notes=f"[dry-run] would greet {target}")
    print(f"Hello, {target}!")
    return HandlerResult(status="completed",
                         notes=f"greeted {target}")


class HelloCampaign:
    id = "hello"

    def __init__(self, issue_repo: str = "YourOrg/playground"):
        self.issue_repo = issue_repo

    def build(self, params: dict) -> CampaignPlan | None:
        target = params.get("target", "world")
        return CampaignPlan(
            title=f"Say hello to {target}",
            intro=f"Demo campaign greeting **{target}**.",
            items=[
                Item(
                    id="greet",
                    title=f"Wave at {target}",
                    handler="hello",            # registered name below
                    params={"target": target},
                ),
            ],
        )
```

Register the handler:

```python
# campaigns/registry.py
from .hello_campaign import HelloCampaign, hello_handler

HANDLERS: dict[str, Callable[[HandlerContext], HandlerResult]] = {
    # ... existing handlers ...
    "hello": hello_handler,
}
```

Handlers should be **idempotent**. They get re-run on every campaign
dispatch. Read the current state of the world (PR open? PyPI shipped?
file already patched?) before deciding to act.

---

## 5. Multi-phase items

Use `phases` when one item has more than one observable milestone, e.g.
a PR that is *opened* and later *merged*. The roadmap diagram shows
each phase as its own checkpoint.

```python
Item(
    id="bump-pr",
    title="Bump dependency X",
    handler="x_bumper",
    phases=("created", "merged"),
)
```

Your handler signals which phase the result applies to via
`HandlerResult.phase`:

```python
def x_bumper_handler(ctx: HandlerContext) -> HandlerResult:
    pr = find_existing_pr(...)
    if pr and pr.merged:
        return HandlerResult(status="completed", pr=pr.number, phase="merged")
    if pr:
        return HandlerResult(status="completed", pr=pr.number, phase="created")
    new_pr = open_pr(...)
    return HandlerResult(status="completed", pr=new_pr, phase="created")
```

For single-phase items, leave `phases` at its default (`("done",)`) and
don't set `phase` in the result.

---

## 6. Dependencies between items

`depends_on` tells the *roadmap diagram* (not the dispatcher) which
items block which. Two forms:

```python
# Tuple shorthand: every dep blocks the item's first phase.
Item(id="b", title="...", depends_on=("a",))

# Phase-aware dict: be explicit about which phase of B depends on which
# phase of A.
Item(
    id="bump",
    title="...",
    phases=("created", "merged"),
    depends_on={
        "merged": ("validator-x", "validator-y", "knack:merged"),
    },
)
```

`"a"` is shorthand for "`a`'s last phase"; `"a:created"` references a
specific milestone.

Note: the dispatcher currently runs **all** handlers on every cycle.
Items whose dependencies haven't completed will simply find nothing to
do (because their idempotency check returns "not yet"). The
`depends_on` metadata is for the diagram, not for ordering.

---

## 7. Validators (lightweight handlers)

Many items don't need a full agent — they're just "check if X is true".
The framework ships two reusable validator handlers; see
[`campaigns/python_upgrade/validators.py`](../campaigns/python_upgrade/validators.py):

| Handler | What it does |
|---|---|
| `pypi_classifier_validator` | Returns `completed` when a PyPI package declares a given `Programming Language :: Python :: X.Y` classifier. |
| `repo_file_validator` | Returns `completed` when a needle is present in a file on a remote repo (via `gh api`). |

Use these for "did the dependency ship?" gates. They're stateless,
free (no LLM), and complete naturally when the world catches up.

---

## 8. Tests

Drop tests under `campaigns/tests/test_<your_campaign>.py`. The
existing tests are a good reference:

- [`test_framework.py`](../campaigns/tests/test_framework.py) — how to
  stub `HandlerContext` and assert state transitions.
- [`test_python_upgrade.py`](../campaigns/tests/test_python_upgrade.py)
  — how to drive `Campaign.build()` with forced params and assert the
  expected items appear.

Run them with:

```pwsh
python -m pytest -q
```

---

## 9. Wiring into workflows

You have three options for triggering your campaign:

1. **Manual dispatch only** — already supported via `RunCampaign.yml`
   once you add the id to the `options` list.
2. **A cron / scheduled wrapper** — copy `PythonUpgradeDaily.yml`,
   change `campaign:` and `params_json:` defaults.
3. **A demo wrapper for forced runs** — copy `PythonUpgradeDemo.yml`
   if your campaign supports forced parameters useful for testing.

All three are thin shims over `RunCampaign.yml`; they don't duplicate
logic.

---

## 10. Checklist before opening a PR

- [ ] Campaign class lives in `campaigns/<name>/__init__.py`.
- [ ] Registered in `campaigns/registry.py` (`CAMPAIGNS` + `HANDLERS`).
- [ ] Added to the `options:` list in `RunCampaign.yml`.
- [ ] Handlers are idempotent (safe to re-run).
- [ ] `--dry-run` does not touch GitHub or the working tree.
- [ ] Tests pass (`python -m pytest -q`).
- [ ] Local dry-run prints the expected plan.
