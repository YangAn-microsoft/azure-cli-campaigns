# Python Upgrade Agent

Automates the mechanical parts of bumping Azure CLI's supported Python minor
version. Runs daily as a GitHub Action. Opens a **draft** PR; humans take it
from there.

See the overall design doc: `AI-Assisted Python Upgrade Pipeline.md` in the
repo root of the planning workspace (not committed here).

## High-level flow

1. **Detect** — parse `build_scripts/windows/scripts/build.cmd` for current
   minor; query python.org for the latest stable patch of the next minor.
2. **Idempotency** — if a PR for that minor already exists (open or closed),
   stop. Closed-without-merge requires human override.
3. **Discover** — `git grep` for the current minor across the repo, excluding
   recordings/locks/agent files.
4. **Few-shot** — fetch the 3 most recent merged "{Packaging} Support Python"
   PRs as examples (falls back to hardcoded #31895/#31928/#33313).
5. **LLM call** — GitHub Models with a strict system prompt; JSON-only output.
6. **Validate** — every edit's path must be a discovered candidate; every
   `old_string` must be unique in its file; ≤ 500 lines / 25 files total.
   One retry on validation failure with the error fed back.
7. **Apply** — create branch from `dev`, apply edits, commit, push, open
   draft PR with `skipped[]` shown to the human as a review checklist.

## Files

| File | Purpose |
|------|---------|
| `agent.py` | Entry point + orchestration + CLI |
| `detect.py` | `build.cmd` parser, python.org feed |
| `discover.py` | git grep candidate collection |
| `ai.py` | GitHub Models call, prompt, few-shot fetch |
| `validate.py` | LLM output validation |
| `github_ops.py` | Thin wrappers over `git` and `gh` CLIs |
| `pr_template.md` | PR body template (Python `str.format`) |
| `tests/` | Unit tests for deterministic modules |

## Local testing

### Unit tests
```bash
cd <azure-cli repo root>
python -m pytest scripts/python_upgrade_agent/tests/ -v
```

### Dry-run against local checkout
Simulates a 3.13 → 3.14 upgrade without touching git or opening a PR:
```bash
# Requires GH_TOKEN with `models: read` (a normal `gh auth token` works).
export GH_TOKEN=$(gh auth token)
export GITHUB_REPOSITORY=Azure/azure-cli  # used only to fetch reference PRs

python -m python_upgrade_agent.agent \
    --force-current-minor 3.13 \
    --force-new-minor 3.14 \
    --force-new-patch 3.14.5 \
    --exclude-pr 33313 \
    --dry-run
```

`--exclude-pr 33313` is important when benchmarking against PR #33313, since
that PR is the ground truth and would otherwise be used as a few-shot example.

### End-to-end on a fork

1. Push this branch to your fork.
2. Set the repo variable `PYTHON_UPGRADE_AGENT_ENABLED=true` (kill-switch
   default is off).
3. Run the workflow via the **Run workflow** button (`workflow_dispatch`).
   Scheduled `cron:` triggers are disabled on forks by GitHub; that is
   expected — the manual button is the test path.
4. Verify the agent opens a draft PR on your fork against your fork's `dev`.

## Configuration

| Setting | Where | Default |
|---------|-------|---------|
| Kill switch | Repo variable `PYTHON_UPGRADE_AGENT_ENABLED` | `false` (disabled) |
| Cron schedule | `.github/workflows/PythonUpgradeAgent.yml` | `0 9 * * *` |
| Model | `ai.DEFAULT_MODEL` | `openai/gpt-4.1` |
| Diff cap | `validate.MAX_FILES`, `validate.MAX_CHANGED_LINES` | 25 files, 500 lines |

## What this agent does NOT do (yet)

- React to CI failures on the PR (Phase 2).
- Edit business logic or test recordings.
- Self-merge or change PR state from draft → ready.
- Run when the previous upgrade PR is closed without merge (requires human
  override — by design).

## Auth

The workflow uses the built-in `GITHUB_TOKEN` with these permissions:

```yaml
permissions:
  contents: write       # branch + commit
  pull-requests: write  # open draft PR
  models: read          # GitHub Models inference
```

No external secrets or GitHub App required for MVP.
