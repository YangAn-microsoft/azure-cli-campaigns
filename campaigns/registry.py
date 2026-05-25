"""Campaign and handler registries.

Adding a new campaign:
1. Implement a class with ``id``, ``issue_repo``, and ``build(params)``.
2. Add it to ``CAMPAIGNS``.
3. If it provides handlers, add them to ``HANDLERS``.
"""
from __future__ import annotations

from typing import Callable

from .base import Campaign, HandlerContext, HandlerResult
from .python_upgrade import (
    PythonUpgradeCampaign,
    azure_cli_upgrader_handler,
    knack_pin_bumper_handler,
    knack_upgrader_handler,
)
from .python_upgrade.validators import (
    pypi_classifier_validator_handler,
    repo_file_validator_handler,
)


CAMPAIGNS: dict[str, Campaign] = {
    "python-upgrade": PythonUpgradeCampaign(),
}

HANDLERS: dict[str, Callable[[HandlerContext], HandlerResult]] = {
    "azure_cli_upgrader": azure_cli_upgrader_handler,
    "knack_upgrader": knack_upgrader_handler,
    "knack_pin_bumper": knack_pin_bumper_handler,
    "pypi_classifier_validator": pypi_classifier_validator_handler,
    "repo_file_validator": repo_file_validator_handler,
    # Backwards-compat alias.
    "python_upgrade_agent": azure_cli_upgrader_handler,
}
