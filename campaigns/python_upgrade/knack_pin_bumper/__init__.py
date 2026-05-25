"""Bumps the ``knack~=X.Y.Z`` pin in ``src/azure-cli-core/setup.py`` to the
latest knack release on PyPI that supports a new Python minor.

This is a separate item from ``azure-cli-bump`` (the embedded Python bump):
the embedded bump opens day 0 to surface CI issues early; this pin bump
waits for the knack maintainer to ship a compatible release to PyPI.
"""
