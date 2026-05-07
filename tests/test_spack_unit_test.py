"""
test_spack_unit_test.py — tests that run ``spack unit-test`` inside the
simulated Pyodide shell environment.

These tests require a full Spack source tree (with the lib/spack/spack/test/
directory present).  They are automatically skipped when the tree is absent.
Set the SPACK_ROOT environment variable to point at a Spack clone before
running, e.g.:

    SPACK_ROOT=/tmp/spack-src pytest tests/test_spack_unit_test.py -v

The CI workflow clones Spack before running the suite, so SPACK_ROOT is
always set there.

Design
------
* Every call goes through run_in_shell() in helpers.py, which spawns a
  fresh subprocess running pyodide_runner.py.  This isolates the
  subprocess mock (installed by shim_system.py) from pytest's own
  process management.
* ``spack unit-test`` is Spack's wrapper around pytest.  When called with
  ``--help`` it shows the subcommand's usage without invoking pytest.  When
  called with test-file arguments it runs the selected tests inside the
  same (mock-patched) process.
"""

import os
import re

import pytest
from helpers import run_in_shell

# Path to the version unit-test file inside a Spack source tree.
# This file tests Spack's pure-Python version-comparison logic and does not
# invoke any subprocess calls that are incompatible with the shim, so it is
# a reliable target for the shimmed environment.
# Path is relative to SPACK_ROOT and matches Spack's own pytest.ini testpath.
_VERSION_TEST = "lib/spack/spack/test/versions.py"

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
        """``spack unit-test --collect-only`` on versions.py finds tests."""
        if not _spack_test_file_present(spack_root, _VERSION_TEST):
            pytest.skip(f"{_VERSION_TEST} not present in {spack_root!r}")

        r = run_in_shell(
            f"spack unit-test --collect-only -q {_VERSION_TEST}",
            timeout=120,
            extra_env={"SPACK_ROOT": spack_root},
        )
        combined = r.stdout + r.stderr
        # pytest --collect-only -q prints one "path::test_name" line per test.
        # Require at least one collected item so "no tests ran" is a failure.
        assert re.search(r"::\w+", combined), (
            f"Expected at least one collected test item in --collect-only output:\n{combined}"
        )


class TestSpackSpec:
    """Test that ``spack spec zlib`` concretizes in the shimmed Pyodide environment.

    ``spack spec zlib`` exercises the full concretization path, including the
    clingo solver.  When clingo is installed as a Python package (via pip) Spack
    detects it through ``importlib.util.find_spec('clingo')`` and skips the
    bootstrap procedure entirely, making it safe to run inside the shimmed
    environment.

    The test is skipped when the spack-packages builtin repository has not been
    cloned into ``$SPACK_ROOT/var/spack/repos/spack_repo/builtin`` (this is
    done by the CI workflow but typically absent in local development).
    """

    # Path relative to SPACK_ROOT where the builtin repo packages live.
    _BUILTIN_PACKAGES = "var/spack/repos/spack_repo/builtin/packages"
    # Package we concretize.
    _PKG = "zlib"

    @pytest.mark.timeout(300)
    def test_spec_zlib(self, spack_root):
        """``spack spec zlib`` must produce a concretized spec containing ``zlib@``."""
        pkg_dir = os.path.join(spack_root, self._BUILTIN_PACKAGES, self._PKG)
        if not os.path.isdir(pkg_dir):
            pytest.skip(
                f"spack-packages builtin repo not present at "
                f"{os.path.join(spack_root, self._BUILTIN_PACKAGES)!r}. "
                "Clone spack-packages and copy builtin/ into "
                "$SPACK_ROOT/var/spack/repos/spack_repo/builtin to run this test."
            )

        r = run_in_shell(
            f"spack spec {self._PKG}",
            timeout=300,
            extra_env={"SPACK_ROOT": spack_root},
        )
        combined = r.stdout + r.stderr
        assert r.returncode == 0, (
            f"spack spec {self._PKG} exited with {r.returncode}.\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )
        # A successful concretization prints "zlib@<version>" in the spec output.
        assert re.search(r"zlib@\S+", combined), (
            f"Expected a concretized zlib spec (e.g. 'zlib@1.3.1') in output.\n"
            f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
        )


class TestSpackUnitTestRun:
    """Run a small, subprocess-free subset of Spack unit tests.

    These tests exercise Spack's pure-Python data model and should pass
    in the shimmed environment where subprocess calls return mock data.
    """

    @pytest.mark.timeout(300)
    def test_version_tests(self, spack_root):
        """Run versions.py — pure-Python version-comparison logic.

        Git-related test cases are excluded because they require either real
        ``git`` subprocess output or on-disk git repositories, neither of
        which is available in the mock environment.  All non-git version
        tests run cleanly in the shimmed environment.
        """
        if not _spack_test_file_present(spack_root, _VERSION_TEST):
            pytest.skip(f"{_VERSION_TEST} not present in {spack_root!r}")

        r = run_in_shell(
            f"spack unit-test {_VERSION_TEST} -q --tb=short"
            " -k 'not git'",
            timeout=300,
            extra_env={"SPACK_ROOT": spack_root},
        )
        combined = r.stdout + r.stderr
        # Require a "N passed" summary and reject any failures or errors.
        assert re.search(r"\d+ passed", combined), (
            f"Expected at least one passing test in pytest summary.\n"
            f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
        )
        failures = re.search(r"\d+ (failed|errors?)", combined)
        assert not failures, (
            f"Tests had unexpected failures or errors: {failures.group()!r}\n"
            f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
        )
