<!--
Intro section for the python-upgrade tracking issue. Rendered into the issue
body above the Plan checklist by scripts/campaigns/python_upgrade.py.

Placeholders ({{name}}) are substituted via str.replace.
-->

This issue tracks the Python {{current_minor}} → {{new_minor}} rollout for
Azure CLI, following the precedent of
[#32869](https://github.com/Azure/azure-cli/issues/32869) (3.14) and
[#29640](https://github.com/Azure/azure-cli/issues/29640) (3.13).

The **`azure-cli-bump`** item below is opened automatically by the
python-upgrade bot. The other items are accumulators — append PR / issue links
as sub-bullets (or in comments below) as work lands, and tick the box once
that workstream is complete.
