"""
conftest.py — shared pytest configuration for spack-lite tests.

Provides:
  - spack_root fixture — the path to a Spack installation tree; tests that
    require Spack are skipped when the tree is absent.

The run_in_shell() helper lives in helpers.py so it can be imported
directly by test modules without relying on conftest import mechanics.
"""

import os

import pytest


@pytest.fixture(scope="session")
def spack_root():
    """Return the path to a Spack installation (with lib/spack present).

    Falls back to the value of the SPACK_ROOT environment variable, then to
    /tmp/spack-src.  Skips the requesting test if neither location contains a
    valid Spack tree.
    """
    root = os.environ.get("SPACK_ROOT", "/tmp/spack-src")
    if not os.path.isdir(os.path.join(root, "lib", "spack")):
        pytest.skip(
            f"Spack not found at {root!r}. "
            "Set the SPACK_ROOT environment variable to a valid Spack tree."
        )
    return root
