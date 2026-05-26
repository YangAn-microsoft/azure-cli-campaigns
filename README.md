# azure-cli-campaigns

Multi-repo rollout framework for Azure CLI. Each *campaign* opens a tracking
issue on a target repo and (optionally) dispatches per-item handlers that can
themselves open PRs. The first campaign, `python-upgrade`, automates the
mechanical part of bumping Azure CLI's embedded Python interpreter.

## Architecture

```
.
├── campaigns/                          # Framework + registered campaigns
│   ├── base.py                         # Item / HandlerContext / Campaign protocol
│   ├── state.py                        # Hidden state-block parser
│   ├── framework.py                    # Generic runner (gh CLI I/O)
│   ├── report.py                       # Roadmap diagram renderer
│   ├── registry.py                     # CAMPAIGNS / HANDLERS lookup tables
│   ├── python_upgrade/                 # The python-upgrade campaign
│   │   ├── __init__.py                 # Campaign class + handler entry points
│   │   ├── intro.md                    # Tracking-issue intro template
│   │   ├── azure_cli_upgrader/         # AI-assisted bump handler
│   │   ├── knack_upgrader/             # knack support PR handler
│   │   ├── knack_pin_bumper/           # azure-cli setup.py pin handler
│   │   └── validators.py               # Reusable PyPI / repo-file checks
│   └── tests/
├── run_campaign.py                     # CLI entry point
└── .github/workflows/
    ├── RunCampaign.yml                 # Manual dispatch + reusable
    ├── PythonUpgradeDaily.yml          # Cron wrapper
    ├── PythonUpgradeDemo.yml           # Forced-version end-to-end demo
    └── PythonUpgradeDemoCleanup.yml    # One-click teardown of a demo run
```

## Running a campaign

### Prerequisites

1. **Fork the target repo** (e.g. `Azure/azure-cli` → `YourOrg/azure-cli`) if
   you don't have push rights. The campaign clones it, branches, pushes, and
   opens a PR.
2. **GitHub App** installed on **both** this campaigns repo and the target
   repo, with `Issues: write`, `Contents: write`, `Pull requests: write`.
   Store `AGENT_APP_ID` and `AGENT_APP_PRIVATE_KEY` as repo secrets.
3. **Repo variable** `RUN_CAMPAIGN_ENABLED=true` on this repo (kill switch).
4. **Repo variable** or input override pointing to your fork(s).

### Manual dispatch

Actions → **Run Campaign** → Run workflow:

- `campaign`: `python-upgrade`
- `target_repo`: `YourOrg/azure-cli`
- `issue_repo`: `YourOrg/azure-cli` (or another repo where the issue lives)
- `params_json`: `{"reference_repo":"Azure/azure-cli","exclude_prs":[33313]}`
- `dry_run`: tick for first runs

### Daily cron

`PythonUpgradeDaily.yml` runs at 09:00 UTC daily and calls RunCampaign with
the `python-upgrade` campaign. Edit the default `target_repo` in that file
for your deployment, or pass overrides via `workflow_dispatch`.

## Forking for experiments

The whole point of this layout is that the campaigns repo is the only thing
you need to fork to experiment safely:

1. Fork `YangAn-microsoft/azure-cli-campaigns` into your namespace.
2. Fork the target repo (e.g. `Azure/azure-cli`) into your namespace.
3. Install your own GitHub App (or use a PAT) on both forks.
4. Set `RUN_CAMPAIGN_ENABLED=true` on your campaigns fork.
5. Dispatch — issues / PRs land on your fork, not upstream.

## Adding a new campaign

See **[docs/adding-a-campaign.md](docs/adding-a-campaign.md)** for the
full walkthrough — anatomy, a hello-world example, multi-phase items,
dependencies, validators, tests, and the workflow wiring checklist.

Short version:

1. Create `campaigns/<name>/__init__.py` with a class implementing the
   `Campaign` protocol (`id`, `issue_repo`, `build(params)`).
2. Optionally add handler functions `(HandlerContext) -> HandlerResult`.
3. Register both in `campaigns/registry.py` (`CAMPAIGNS` + `HANDLERS`).
4. Add the campaign id to the `options:` list in
   `.github/workflows/RunCampaign.yml`.
5. Drop tests under `campaigns/tests/`.

## Local development

```bash
pip install -r requirements.txt   # stdlib only; pytest for tests
python -m pytest -q

# Dry-run a campaign locally
$env:GH_TOKEN = (gh auth token)
python -m run_campaign --campaign python-upgrade --dry-run \
    --params '{"current_full":"3.13.13","new_full":"3.14.5","handler_repo":"YourOrg/azure-cli"}' \
    --issue-repo YourOrg/azure-cli
```
