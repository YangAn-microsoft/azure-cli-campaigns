"""LLM call: GitHub Models inference with the upgrade prompt.

Inputs:
    - current/new minor + new patch
    - Reference PR diffs (few-shot)
    - Discovered candidate list with file context

Output (validated downstream):
    {
      "edits":   [{path, old_string, new_string, reason}, ...],
      "skipped": [{path, location, snippet, why}, ...],
      "notes":   "..."
    }
"""
from __future__ import annotations

import json
import os
import subprocess
import urllib.request
from dataclasses import dataclass

from .detect import Version
from .discover import candidates_summary, Candidate

# GitHub Models inference endpoint. Uses the workflow's GITHUB_TOKEN with
# `models: read` permission. Future migration to Azure OpenAI is a URL swap.
MODELS_URL = "https://models.github.ai/inference/chat/completions"
DEFAULT_MODEL = "openai/gpt-4.1"

# Fallback reference PRs (azure-cli) if the GitHub query returns fewer than 3.
FALLBACK_REFERENCE_PRS = [33313, 31928, 31895]

# Lines in `git diff` / `gh pr diff` output that carry no semantic value for
# few-shot learning (file index hashes, rename hints, blank separator lines).
# Stripping them shaves ~15% off each reference PR diff without information loss.
_NOISE_PREFIXES = (
    "index ",
    "similarity index ",
    "dissimilarity index ",
    "rename from ",
    "rename to ",
    "copy from ",
    "copy to ",
    "old mode ",
    "new mode ",
    "deleted file mode ",
    "new file mode ",
)


@dataclass(frozen=True)
class ReferencePR:
    number: int
    title: str
    diff: str


def fetch_reference_prs(
    repo: str,
    exclude: set[int] | None = None,
    limit: int = 3,
) -> list[ReferencePR]:
    """Query the most recent merged '{Packaging} Support Python ...' PRs via `gh`."""
    exclude = exclude or set()
    # gh search prs supports the same query language as the GitHub web UI.
    cmd = [
        "gh", "search", "prs",
        f"repo:{repo}",
        "is:merged",
        "in:title", "{Packaging} Support Python",
        "--sort", "updated",
        "--order", "desc",
        "--limit", str(limit * 3),
        "--json", "number,title",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, encoding="utf-8")
        rows = json.loads(result.stdout or "[]")
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        rows = []

    selected: list[ReferencePR] = []
    for row in rows:
        if len(selected) >= limit:
            break
        n = row.get("number")
        if not isinstance(n, int) or n in exclude:
            continue
        diff = _fetch_pr_diff(repo, n)
        if diff:
            selected.append(ReferencePR(number=n, title=row.get("title", ""), diff=diff))

    if len(selected) < limit:
        for n in FALLBACK_REFERENCE_PRS:
            if len(selected) >= limit:
                break
            if n in exclude or any(pr.number == n for pr in selected):
                continue
            diff = _fetch_pr_diff(repo, n)
            if diff:
                selected.append(ReferencePR(number=n, title=f"PR #{n}", diff=diff))
    return selected


def _fetch_pr_diff(repo: str, number: int) -> str:
    """Fetch a PR's unified diff via `gh pr diff`, stripped of noise lines.

    Removes file index hashes (`index abc..def`), rename/copy/mode markers,
    and blank separator lines between hunks. The +/- content and `@@` hunk
    headers — the actual few-shot signal — are preserved verbatim.
    """
    try:
        result = subprocess.run(
            ["gh", "pr", "diff", str(number), "--repo", repo],
            capture_output=True, text=True, check=True, encoding="utf-8",
        )
    except subprocess.CalledProcessError:
        return ""

    kept: list[str] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        if line.startswith(_NOISE_PREFIXES):
            continue
        kept.append(line)
    return "\n".join(kept) + "\n"


SYSTEM_PROMPT = """\
You are an automated agent that upgrades Azure CLI's Python version references.

You will be given:
- Current Python minor (e.g. "3.13") and new minor (e.g. "3.14").
- The latest patch version of the new minor (e.g. "3.14.5").
- Diffs of recent merged PRs that performed similar upgrades, as examples.
- A list of candidate locations in the repository (file + line + context) that
  reference the current minor.

Your task: decide, for each candidate, whether to edit it, skip it, or mark it uncertain.

Rules (strict):
1. Only update Python version strings. Do not refactor logic. Do not add or
   reword comments unless the reference PRs do so for the same reason.
2. Some reference PRs contain non-mechanical changes (e.g. _pth handling tweaks).
   These were one-off human fixes for specific minors. Do NOT replicate analogous
   changes.
3. For full X.Y.Z version strings (e.g. PYTHON_VERSION=3.13.13), use the new
   patch version. For X.Y-only strings, use the new minor. Do not invent
   patch versions where the original had only X.Y.
4. CI matrix policy: when a CI job (Azure Pipelines / GitHub Actions) is
   parameterised by Python version, replace the prior minor with the new minor
   (per reference PRs). Do not keep both. When the matrix entry's KEY also
   embeds the minor (e.g. `Python313:` containing `python.version: '3.13'`),
   rename the key too (`Python314:` containing `'3.14'`). Same for job IDs and
   displayNames that embed the minor with no dot (e.g.
   `AutomationFullTestPython313ProfileLatest` → `...Python314...`).
   This rule applies ONLY to CI matrix/job parameterisation, not to package
   metadata (see rule 11).
5. If uncertain about whether a match should be updated, put it in `skipped`.
   Do NOT edit anything you are unsure about.
6. Never propose edits to files outside the candidate list.
7. Each `old_string` must be unique in its file UNLESS you also set
   `"replace_all": true`, in which case every occurrence in that file will
   be replaced. **STRONGLY PREFER the smallest unique `old_string` + `replace_all: true`
   over multi-line context blocks.** When the same short pattern recurs across
   sibling sections (e.g. `      Python313:` matrix key appears in 5 jobs,
   `versionSpec: '3.13'` appears in 2 stages), the correct edit is a one-line
   `old_string` with `replace_all: true` — NOT a multi-line `old_string` that
   bundles adjacent context lines (those usually match multiple times too and
   fail validation). Reserve multi-line `old_string` only for patterns that
   are genuinely one-of-a-kind in the file.
8. Output JSON only, matching the schema. No prose outside JSON.
9. TRUST THE CANDIDATE TEXT AS-IS. Every candidate line below was produced by
   `git grep` for the current minor; the current minor literally appears in
   that line. Do NOT claim a line "already references the new minor" — if it
   did, it would not be in the candidate list. Your job is to decide whether
   that current-minor reference should be bumped to the new minor, kept
   verbatim (e.g. historical changelog, comment about a specific past version),
   or skipped as uncertain.
10. Reasonable defaults when in doubt:
    - CI YAML / pipeline / setup.py / shell scripts referencing the current
      minor as the active build target → EDIT (bump to new minor).
    - HISTORY.rst / CHANGELOG / past-tense comments → SKIP (historical).
    - Hand-written docs describing supported versions → EDIT (bump).
    - Embedded patch versions in URLs/filenames where original is X.Y.Z → EDIT
      using the new patch.
11. setup.py classifier lists are ADDITIVE. When a `setup.py` `classifiers`
    list contains
        'Programming Language :: Python :: <current_minor>',
    do NOT replace it. INSERT a new sibling line for the new minor instead.
    Express the edit so `new_string` contains BOTH the current-minor line
    (unchanged) and the new-minor line appended on the next line, preserving
    indentation and the trailing comma. Example:
        old_string:
            "    'Programming Language :: Python :: 3.13',"
        new_string:
            "    'Programming Language :: Python :: 3.13',\n    'Programming Language :: Python :: 3.14',"
    CRITICAL: `old_string` must appear as an EXACT substring of `new_string`.
    Do NOT mutate the preserved line. In particular:
        WRONG: "    'Programming Language :: Python :: Python :: 3.13',\n    'Programming Language :: Python :: 3.14',"
               (the LLM duplicated `Python ::` while typing out the preserved line)
        WRONG: "    'Programming Language :: Python :: 3.14',"
               (replaced instead of appended — drops 3.13 support)
    The package keeps advertising support for the prior minor; downstream
    maintainers drop it in a separate cycle.
12. Preserve surrounding YAML quoting style. If neighbouring values for the
    same key (e.g. `versionSpec: '3.12'`) are single-quoted, single-quote the
    new value too. If unquoted, leave unquoted. Do not change quoting style
    unilaterally — it produces noise in diffs and inconsistency in the file.
13. Historical-context comments are SKIP, not edit. When the current minor
    appears inside a comment that explains WHY a past change was made and
    cites a specific PR / issue URL (e.g.
        `# serviceconnector-passwordless's dependency is not compatible with 3.13 https://github.com/Azure/azure-cli/pull/31895`
    or
        `# Disable foo: https://github.com/Azure/azure-cli/issues/12345 — broken on 3.12`),
    the version number is part of the historical record describing the linked
    PR/issue. Do NOT bump it. Put the candidate in `skipped` with
    why = "historical comment referencing past PR/issue; version is part of
    the recorded reason, not a live build target". This rule overrides rule
    10's "shell scripts → EDIT" default whenever the line is a `#` comment
    containing a GitHub PR/issue URL.

Schema:
{
  "edits": [
    {"path": "...", "old_string": "...", "new_string": "...",
     "reason": "...", "replace_all": false}
  ],
  "skipped": [
    {"path": "...", "location": "line N", "snippet": "...", "why": "..."}
  ],
  "notes": "free-form summary, may be empty"
}
"""


def build_user_message(
    current: Version,
    target: Version,
    candidates: list[Candidate],
    references: list[ReferencePR],
    retry_error: str | None = None,
) -> str:
    parts: list[str] = []
    parts.append(f"Current minor: {current.minor_str}")
    parts.append(f"New minor:     {target.minor_str}")
    parts.append(f"New patch:     {target.full_str}")
    parts.append("")
    parts.append("## Reference PRs (most recent first)")
    for ref in references:
        parts.append(f"### PR #{ref.number} — {ref.title}")
        parts.append("```diff")
        parts.append(ref.diff)
        parts.append("```")
    parts.append("")
    parts.append("## Candidates")
    parts.append(candidates_summary(candidates))
    if retry_error:
        parts.append("")
        parts.append("## Previous attempt failed validation. Fix and try again.")
        parts.append(retry_error)
    return "\n".join(parts)


def call_model(
    system: str,
    user: str,
    model: str = DEFAULT_MODEL,
    token: str | None = None,
) -> str:
    """POST to GitHub Models chat completions and return the assistant text."""
    # Prefer MODELS_TOKEN (dedicated, must have models:read) over GH_TOKEN.
    # GH_TOKEN may be a GitHub App installation token that lacks models:read.
    token = (
        token
        or os.environ.get("MODELS_TOKEN")
        or os.environ.get("GH_TOKEN")
        or os.environ.get("GITHUB_TOKEN")
    )
    if not token:
        raise RuntimeError("MODELS_TOKEN / GH_TOKEN / GITHUB_TOKEN not set")
    body = json.dumps({
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }).encode("utf-8")
    req = urllib.request.Request(
        MODELS_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310
        payload = json.load(resp)
    return payload["choices"][0]["message"]["content"]


def parse_model_output(text: str) -> dict:
    """Parse the model's JSON output, tolerating stray prose by stripping fences."""
    s = text.strip()
    if s.startswith("```"):
        s = s.strip("`")
        # remove leading 'json' tag if any
        first_nl = s.find("\n")
        if first_nl != -1 and s[:first_nl].strip().lower() in {"json", ""}:
            s = s[first_nl + 1:]
        if s.endswith("```"):
            s = s[:-3]
    return json.loads(s)
