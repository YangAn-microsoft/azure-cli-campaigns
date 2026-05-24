"""Campaign framework: multi-repo rollouts driven by a tracking issue.

A *campaign* (e.g. ``python-upgrade``) declares a plan issue title, an intro
body, and a list of work items. Some items have a handler that can run
automation (e.g. open a PR); others are manual checkboxes. The framework
creates/finds the plan issue, dispatches handlers, and keeps the issue in
sync with structured state embedded as a hidden HTML comment.
"""
