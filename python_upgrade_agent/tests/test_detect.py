import pytest

from python_upgrade_agent.detect import (
    UpgradeDecision,
    Version,
    decide_upgrade,
    parse_build_cmd,
    parse_python_org_feed,
)


BUILD_CMD_SAMPLE = """\
@echo off
if "%ARCH%"=="x86" (
    set PYTHON_ARCH=win32
) else if "%ARCH%"=="x64" (
    set PYTHON_ARCH=amd64
)
set PYTHON_VERSION=3.13.13

set WIX_DOWNLOAD_URL=...
"""


def test_parse_build_cmd_basic():
    v = parse_build_cmd(BUILD_CMD_SAMPLE)
    assert v == Version(3, 13, 13)
    assert v.minor_str == "3.13"
    assert v.full_str == "3.13.13"


def test_parse_build_cmd_missing_raises():
    with pytest.raises(ValueError):
        parse_build_cmd("no version here")


def test_parse_python_org_feed_filters_pre_release():
    payload = [
        {"name": "Python 3.13.13", "pre_release": False},
        {"name": "Python 3.14.5", "pre_release": False},
        {"name": "Python 3.14.0rc1", "pre_release": True},
        {"name": "Python 3.15.0a1", "pre_release": False},  # caught by name regex
        {"name": "Python 3.13.12", "pre_release": False},
        {"name": "Python 2.7.18", "pre_release": False},
        {"name": "Something Else", "pre_release": False},
    ]
    result = parse_python_org_feed(payload)
    assert result[(3, 13)] == Version(3, 13, 13)
    assert result[(3, 14)] == Version(3, 14, 5)
    assert (3, 15) not in result  # only saw an alpha


def test_decide_upgrade_next_minor():
    current = Version(3, 13, 13)
    releases = {
        (3, 13): Version(3, 13, 13),
        (3, 14): Version(3, 14, 5),
        (3, 15): Version(3, 15, 1),
    }
    decision = decide_upgrade(current, releases)
    assert decision is not None
    assert decision.target == Version(3, 14, 5)  # next minor, not highest
    assert decision.needed


def test_decide_upgrade_none_when_up_to_date():
    current = Version(3, 14, 5)
    releases = {(3, 14): Version(3, 14, 5)}
    assert decide_upgrade(current, releases) is None


def test_decide_upgrade_none_when_next_unreleased():
    current = Version(3, 13, 13)
    releases = {(3, 13): Version(3, 13, 13)}  # 3.14 not released yet
    assert decide_upgrade(current, releases) is None


def test_version_ordering():
    assert Version(3, 13, 13) < Version(3, 14, 0)
    assert Version(3, 14, 5) < Version(3, 14, 6)
    assert not (Version(3, 14, 5) < Version(3, 14, 5))
