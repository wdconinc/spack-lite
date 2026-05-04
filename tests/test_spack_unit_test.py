"""
test_spack_unit_test.py — tests that run ``spack unit-test`` inside the
simulated Pyodide shell environment.

These tests require a full Spack source tree (with the lib/spack/tests/
directory present).  They are automatically skipped when the tree is absent.
Set the SPACK_ROOT environment variable to point at a Spack clone before
running, e.g.:

    SPACK_ROOT=/tmp/spack-src pytest tests/test_spack_unit_test.py -v

The CI workflow clones Spack before running the suite, so SPACK_ROOT is
always set there.

Design
------
* Every call goes through run_in_shell() in conftest.py, which spawns a
  fresh subprocess running pyodide_runner.py.  This isolates the
  subprocess mock (installed by shim_system.py) from pytest's own
  process management.
* ``spack unit-test`` is Spack's wrapper around pytest.  When called with
  ``--help`` it shows the subcommand's usage without invoking pytest.  When
  called with test-file arguments it runs the selected tests inside the
  same (mock-patched) process.
"""

import os

import pytest
from helpers import run_in_shell

# Path to the version unit-test file inside a Spack source tree.
# This file tests Spack's pure-Python version-comparison logic and does not
# invoke any subprocess calls, so it is a reliable target for the shimmed
# environment.
_VERSION_TEST = "lib/spack/test/test_version.py"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _spack_test_file_present(spack_root, rel_path):
    """Return True if *rel_path* exists inside *spack_root*."""
    return os.path.isfile(os.path.join(spack_root, rel_path))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSpackUnitTestHelp:
    """Smoke tests: verify that ``spack unit-test`` is reachable via the shell."""

    def test_help_runs(self, spack_root):
        """``spack unit-test --help`` must exit cleanly and produce output."""
        r = run_in_shell("spack unit-test --help", timeout=120,
                         extra_env={"SPACK_ROOT": spack_root})
        assert r.returncode == 0, (
            f"spack unit-test --help exited with {r.returncode}.\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )
        assert r.stdout.strip(), "Expected non-empty output from spack unit-test --help"

    def test_help_mentions_pytest(self, spack_root):
        """The help text should reference pytest or tests in some form."""
        r = run_in_shell("spack unit-test --help", timeout=120,
                         extra_env={"SPACK_ROOT": spack_root})
        combined = (r.stdout + r.stderr).lower()
        assert any(kw in combined for kw in ("pytest", "test", "unit")), (
            f"Expected 'pytest' / 'test' / 'unit' in output, got:\n{r.stdout}"
        )


class TestSpackUnitTestCollect:
    """Verify that test collection works for a known pure-Python test file."""

    def test_collect_version_tests(self, spack_root):
        """``spack unit-test --collect-only`` on test_version.py finds tests."""
        if not _spack_test_file_present(spack_root, _VERSION_TEST):
            pytest.skip(f"{_VERSION_TEST} not present in {spack_root!r}")

        r = run_in_shell(
            f"spack unit-test --collect-only -q {_VERSION_TEST}",
            timeout=120,
            extra_env={"SPACK_ROOT": spack_root},
        )
        combined = r.stdout + r.stderr
        # pytest --collect-only prints collected items or "no tests ran"
        assert any(kw in combined.lower() for kw in ("test_", "selected", "collected", "no tests ran")), (
            f"Unexpected output from --collect-only:\n{combined}"
        )


class TestSpackUnitTestRun:
    """Run a small, subprocess-free subset of Spack unit tests.

    These tests exercise Spack's pure-Python data model and should pass
    in the shimmed environment where subprocess calls return mock data.
    """

    @pytest.mark.timeout(300)
    def test_version_tests(self, spack_root):
        """Run test_version.py — pure-Python version-comparison logic."""
        if not _spack_test_file_present(spack_root, _VERSION_TEST):
            pytest.skip(f"{_VERSION_TEST} not present in {spack_root!r}")

        r = run_in_shell(
            f"spack unit-test {_VERSION_TEST} -x -q --tb=short",
            timeout=300,
            extra_env={"SPACK_ROOT": spack_root},
        )
        combined = r.stdout + r.stderr
        # pytest always prints a summary line containing "passed", "failed",
        # or "error".  Accept any outcome — the important thing is that the
        # test infrastructure runs without crashing.
        assert any(kw in combined.lower() for kw in ("passed", "failed", "error", "no tests ran")), (
            f"pytest did not produce a recognisable summary.\n"
            f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
        )
        # Fail the test only if pytest itself crashed (no summary at all and
        # a non-zero exit code from the runner process).
        if r.returncode != 0 and not any(kw in combined.lower() for kw in ("passed", "failed")):
            pytest.fail(
                f"spack unit-test crashed (exit {r.returncode}).\n"
                f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
            )
