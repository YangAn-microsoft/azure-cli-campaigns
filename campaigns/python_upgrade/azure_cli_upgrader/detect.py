"""Detect current and target Python versions.

Current minor is parsed from build_scripts/windows/scripts/build.cmd (authoritative
source for the Python version embedded in the Windows MSI).

Target minor/patch are obtained from python.org's release feed, filtered to
stable releases only.
"""
from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path

BUILD_CMD_PATH = Path("build_scripts/windows/scripts/build.cmd")

# Matches lines like: `set PYTHON_VERSION=3.13.13`
_BUILD_CMD_VERSION_RE = re.compile(
    r"^\s*set\s+PYTHON_VERSION\s*=\s*(\d+)\.(\d+)\.(\d+)\s*$",
    re.MULTILINE,
)

# python.org release feed. We use the public web API.
# Each entry has fields like: name "Python 3.14.5", version (id of version object),
# is_published, pre_release, release_date, etc.
PYTHON_ORG_RELEASES_URL = (
    "https://www.python.org/api/v2/downloads/release/"
    "?is_published=true&pre_release=false"
)


@dataclass(frozen=True)
class Version:
    major: int
    minor: int
    patch: int

    @property
    def minor_str(self) -> str:
        return f"{self.major}.{self.minor}"

    @property
    def full_str(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    def __lt__(self, other: "Version") -> bool:
        return (self.major, self.minor, self.patch) < (other.major, other.minor, other.patch)


@dataclass(frozen=True)
class UpgradeDecision:
    """Result of comparing current vs latest Python."""
    current: Version
    target: Version  # latest patch of the next unsupported minor

    @property
    def needed(self) -> bool:
        return (self.target.major, self.target.minor) > (self.current.major, self.current.minor)


def parse_build_cmd(content: str) -> Version:
    """Parse PYTHON_VERSION=X.Y.Z from build.cmd content."""
    m = _BUILD_CMD_VERSION_RE.search(content)
    if not m:
        raise ValueError("Could not find 'set PYTHON_VERSION=X.Y.Z' in build.cmd")
    return Version(int(m.group(1)), int(m.group(2)), int(m.group(3)))


def read_current_version(repo_root: Path | None = None) -> Version:
    """Read current Python version from build.cmd in the given repo root (default: CWD)."""
    root = repo_root or Path.cwd()
    path = root / BUILD_CMD_PATH
    return parse_build_cmd(path.read_text(encoding="utf-8"))


def parse_python_org_feed(payload: list[dict]) -> dict[tuple[int, int], Version]:
    """Group releases by (major, minor) and return the highest patch per minor.

    Filters out anything that smells like a pre-release even if the feed flag
    is set wrong (defensive).
    """
    by_minor: dict[tuple[int, int], Version] = {}
    pre_re = re.compile(r"(?i)(rc|alpha|beta|a\d|b\d)")
    for entry in payload:
        name = entry.get("name", "")
        if entry.get("pre_release") or pre_re.search(name):
            continue
        m = re.match(r"^Python\s+(\d+)\.(\d+)\.(\d+)$", name.strip())
        if not m:
            continue
        v = Version(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        key = (v.major, v.minor)
        if key not in by_minor or v > by_minor[key]:
            by_minor[key] = v
    return by_minor


def fetch_python_releases(url: str = PYTHON_ORG_RELEASES_URL) -> dict[tuple[int, int], Version]:
    """Fetch and parse python.org's release feed."""
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        payload = json.load(resp)
    return parse_python_org_feed(payload)


def decide_upgrade(
    current: Version,
    releases: dict[tuple[int, int], Version],
) -> UpgradeDecision | None:
    """Pick the *next* unsupported minor, not the highest.

    Per plan: if multiple unsupported minors exist (e.g. repo on 3.13, both
    3.14 and 3.15 released), we upgrade to 3.14 first. After that merges,
    the next daily run picks up 3.15.
    """
    next_key = (current.major, current.minor + 1)
    target = releases.get(next_key)
    if target is None:
        return None  # next minor not yet released as stable
    if (target.major, target.minor) <= (current.major, current.minor):
        return None
    return UpgradeDecision(current=current, target=target)
