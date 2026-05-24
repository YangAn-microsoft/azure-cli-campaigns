"""Campaign and handler registries.

Adding a new campaign:
1. Implement a class with ``id``, ``issue_repo``, and ``build(params)``.
2. Add it to ``CAMPAIGNS``.
3. If it provides handlers, add them to ``HANDLERS``.
"""
from __future__ import annotations

from typing import Callable

from .base import Campaign, HandlerContext, HandlerResult
from .python_upgrade import PythonUpgradeCampaign, python_upgrade_handler


CAMPAIGNS: dict[str, Campaign] = {
    "python-upgrade": PythonUpgradeCampaign(),
}

HANDLERS: dict[str, Callable[[HandlerContext], HandlerResult]] = {
    "python_upgrade_agent": python_upgrade_handler,
}
