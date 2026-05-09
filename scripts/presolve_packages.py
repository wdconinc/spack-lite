#!/usr/bin/env python3
"""
scripts/presolve_packages.py — Build-time concretization cache pre-population.

Pre-solves each seed package listed on the command line using the spack-lite
tree at SPACK_LITE_DIR, then stores the results in Spack's concretization
cache inside that tree.  When the browser later runs ``spack spec <pkg>`` the
solver finds a cache hit and skips the expensive clingo load/ground/solve
phases.

Usage:
    python3 scripts/presolve_packages.py <SPACK_LITE_DIR> <pkg1> [pkg2 …]

Environment variables used internally (set by the script itself):
    SPACK_ROOT              → SPACK_LITE_DIR
    SPACK_USER_CONFIG_PATH  → temporary per-run directory deleted on exit

Subprocess / OS monkey-patches
-------------------------------
The script patches ``subprocess.run``, ``subprocess.Popen``, and
``subprocess.check_output`` to return the **same** canned responses that
``shim_system.py`` produces in the browser.  This ensures the ASP facts
generated for the compiler (gcc@11.4.0, glibc@2.35) are byte-for-byte
identical between the build machine and the browser, so the cache keys
produced here match the ones the browser computes at runtime.

Two ``os.path`` functions are also patched:
  * ``os.path.exists``  — returns True for /lib64/ld-linux-x86-64.so.2 even
                           when the file is absent on the build machine.
  * ``os.path.realpath`` — returns the path unchanged for the same file so
                           that Spack's prefix extraction yields "/" (same as
                           the browser, where the file is a plain MEMFS stub).
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import types

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FAKE_LD_PATH = "/lib64/ld-linux-x86-64.so.2"
_KNOWN_OVERFLOW_ERROR = "signed integer is greater than maximum"


def _cmd_str(args) -> str:
    """Return a single string representation of args for pattern matching."""
    if isinstance(args, (list, tuple)):
        return " ".join(str(a) for a in args)
    return str(args or "")


def _should_intercept(args) -> bool:
    cmd = _cmd_str(args)
    return any(
        k in cmd
        for k in (
            "ld-linux-x86-64.so.2",
            "ld.so",
            "/usr/bin/gcc",
            "/usr/bin/g++",
            "/usr/bin/gfortran",
        )
    )


def _make_fake_completed_process(args):
    """Return a subprocess.CompletedProcess-compatible object with shimmed output."""
    cmd = _cmd_str(args)
    stdout = b""
    stderr = b""
    returncode = 0

    if "ld-linux-x86-64.so.2" in cmd or "ld.so" in cmd:
        if "--version" in cmd:
            # spack.util.libc._libc_from_dynamic_linker detects glibc from this.
            stdout = (
                b"ld.so (GNU C Library) stable release version 2.35.\n"
                b"Copyright (C) 2022 Free Software Foundation, Inc.\n"
            )
        # --help  →  no output (empty search-path list is fine)
    elif "/usr/bin/gcc" in cmd or ("/usr/bin/cc" in cmd and "clang" not in cmd):
        if " -v " in cmd and " -o " in cmd:
            # CompilerPropertyDetector._compile_dummy_c_source: needs
            # "-dynamic-linker /lib64/ld-linux-x86-64.so.2" in stderr so
            # spack.util.libc.parse_dynamic_linker() finds the linker path.
            stderr = (
                b"gcc version 11.4.0 (Ubuntu 11.4.0-1ubuntu1~22.04)\n"
                b" /usr/lib/gcc/x86_64-linux-gnu/11/collect2"
                b" -dynamic-linker /lib64/ld-linux-x86-64.so.2\n"
            )
        else:
            stdout = b"gcc (Ubuntu 11.4.0-1ubuntu1~22.04) 11.4.0\n"
    elif "/usr/bin/g++" in cmd or "/usr/bin/c++" in cmd:
        stdout = b"g++ (Ubuntu 11.4.0-1ubuntu1~22.04) 11.4.0\n"
    elif "/usr/bin/gfortran" in cmd:
        stdout = b"GNU Fortran (Ubuntu 11.4.0-1ubuntu1~22.04) 11.4.0\n"

    return types.SimpleNamespace(
        args=args,
        stdout=stdout,
        stderr=stderr,
        returncode=returncode,
    )


# ---------------------------------------------------------------------------
# Monkey-patch subprocess
# ---------------------------------------------------------------------------

_real_run = subprocess.run
_real_check_output = subprocess.check_output
_real_popen_cls = subprocess.Popen


def _patched_run(args=None, *extra, **kwargs):
    if _should_intercept(args):
        return _make_fake_completed_process(args)
    return _real_run(args, *extra, **kwargs)


def _patched_check_output(args=None, *extra, **kwargs):
    if _should_intercept(args):
        return _make_fake_completed_process(args).stdout
    return _real_check_output(args, *extra, **kwargs)


class _FakePopen:
    """Minimal Popen replacement for intercepted compiler/linker calls."""

    def __init__(self, args=None, *extra, **kwargs):
        r = _make_fake_completed_process(args)
        self._stdout_bytes = r.stdout
        self._stderr_bytes = r.stderr
        self.returncode = r.returncode
        self.pid = 99999
        # Some callers pass stdout=PIPE / stderr=PIPE and then read .stdout
        self.stdout = io.BytesIO(self._stdout_bytes) if self._stdout_bytes else None
        self.stderr = io.BytesIO(self._stderr_bytes) if self._stderr_bytes else None

    def communicate(self, input_data=None, timeout=None):
        return self._stdout_bytes, self._stderr_bytes

    def wait(self, timeout=None):
        return self.returncode

    def poll(self):
        return self.returncode

    def kill(self):
        pass

    def terminate(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def _patched_popen(args=None, *extra, **kwargs):
    if _should_intercept(args):
        return _FakePopen(args, *extra, **kwargs)
    return _real_popen_cls(args, *extra, **kwargs)


subprocess.run = _patched_run
subprocess.check_output = _patched_check_output
subprocess.Popen = _patched_popen  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Monkey-patch os.path for /lib64/ld-linux-x86-64.so.2
# ---------------------------------------------------------------------------
# spack.util.libc._libc_from_dynamic_linker checks os.path.exists() before
# probing the dynamic linker.  On build machines where the file may be absent
# (or has an unexpected realpath) we fake both functions to guarantee:
#   • exists()   → True
#   • realpath() → /lib64/ld-linux-x86-64.so.2  (no symlink resolution)
# This gives prefix = dirname("/lib64") = "/" which matches the browser, where
# the shim creates the file as a plain MEMFS stub at that exact path.

_real_exists = os.path.exists
_real_realpath = os.path.realpath


def _patched_exists(path):
    if str(path) == _FAKE_LD_PATH:
        return True
    return _real_exists(path)


def _patched_realpath(path, *args, **kwargs):
    if str(path) == _FAKE_LD_PATH:
        return _FAKE_LD_PATH
    return _real_realpath(path, *args, **kwargs)


os.path.exists = _patched_exists
os.path.realpath = _patched_realpath

# ---------------------------------------------------------------------------
# Configuration templates (mirrors worker.js / spack_config/)
# ---------------------------------------------------------------------------

_CONFIG_YAML = textwrap.dedent(
    """\
    config:
      concretizer: clingo
      checksum: false
      verify_ssl: false
      install_missing_compilers: false
      build_jobs: 1
      db_lock_timeout: 60
    """
)

_COMPILERS_YAML = textwrap.dedent(
    """\
    compilers:
    - compiler:
        spec: gcc@11.4.0
        paths:
          cc: /usr/bin/gcc
          cxx: /usr/bin/g++
          f77: /usr/bin/gfortran
          fc: /usr/bin/gfortran
        flags: {}
        operating_system: ubuntu22.04
        target: x86_64
        modules: []
        environment: {}
        extra_rpaths: []
    """
)

_PACKAGES_YAML = textwrap.dedent(
    """\
    packages:
      all:
        target: [x86_64]
        providers:
          mpi: [openmpi]
          blas: [openblas]
          lapack: [openblas]
    """
)

# Concretizer config for the pre-solve: enable the cache and point it at the
# concretization_cache directory inside the spack-lite tree ($spack expands
# to SPACK_ROOT at runtime, which equals SPACK_LITE_DIR here and
# /home/pyodide/spack in the browser).
_CONCRETIZER_YAML_TEMPLATE = textwrap.dedent(
    """\
    concretizer:
      reuse: false
      concretization_cache:
        enable: true
        url: {cache_url}
    """
)

# repos.yaml is written referencing the absolute path of the spack-lite tree
# so that Spack can find the package recipes without hitting the network.
_REPOS_YAML_TEMPLATE = textwrap.dedent(
    """\
    repos:
      builtin: {builtin_path}
    """
)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    if len(sys.argv) < 3:
        print(
            "Usage: presolve_packages.py <SPACK_LITE_DIR> <pkg1> [pkg2 …]",
            file=sys.stderr,
        )
        return 1

    spack_lite_dir = os.path.realpath(sys.argv[1])
    packages = sys.argv[2:]

    if not os.path.isdir(os.path.join(spack_lite_dir, "lib", "spack")):
        print(
            f"ERROR: {spack_lite_dir} does not look like a Spack tree "
            f"(missing lib/spack/).",
            file=sys.stderr,
        )
        return 1

    # Check clingo is importable before we go further.
    try:
        import clingo  # noqa: F401
    except ImportError:
        print(
            "WARNING: clingo not available — skipping pre-solve.\n"
            "         Install clingo (pip install clingo) to enable build-time caching.",
            file=sys.stderr,
        )
        return 0

    # ------------------------------------------------------------------
    # Set up SPACK_ROOT and a temporary user-config directory
    # ------------------------------------------------------------------
    cache_dir = os.path.join(spack_lite_dir, "var", "spack", "concretization_cache")
    os.makedirs(cache_dir, exist_ok=True)

    # Use an absolute path for the cache URL so it doesn't depend on cwd.
    # In the browser we use $spack/var/spack/concretization_cache (resolved
    # by Spack's substitute_config_variables).  Here we use the absolute path
    # directly because the browser's $spack expansion is equivalent.
    cache_url = cache_dir

    builtin_path = os.path.join(
        spack_lite_dir, "var", "spack", "repos", "spack_repo", "builtin"
    )

    home_dir = tempfile.mkdtemp(prefix="spack-lite-presolve-")
    try:
        os.makedirs(os.path.join(home_dir, "linux"), exist_ok=True)

        _write(os.path.join(home_dir, "config.yaml"), _CONFIG_YAML)
        _write(
            os.path.join(home_dir, "concretizer.yaml"),
            _CONCRETIZER_YAML_TEMPLATE.format(cache_url=cache_url),
        )
        _write(
            os.path.join(home_dir, "linux", "compilers.yaml"),
            _COMPILERS_YAML,
        )
        _write(os.path.join(home_dir, "packages.yaml"), _PACKAGES_YAML)
        _write(
            os.path.join(home_dir, "repos.yaml"),
            _REPOS_YAML_TEMPLATE.format(builtin_path=builtin_path),
        )

        os.environ["SPACK_ROOT"] = spack_lite_dir
        os.environ["SPACK_USER_CONFIG_PATH"] = home_dir

        # Add spack to sys.path
        spack_lib = os.path.join(spack_lite_dir, "lib", "spack")
        spack_ext = os.path.join(spack_lite_dir, "lib", "spack", "external")
        for p in (spack_lib, spack_ext):
            if p not in sys.path:
                sys.path.insert(0, p)

        # Import spack.main after patching and after sys.path is set so that
        # all downstream imports (spack.util.libc etc.) see our patches.
        import spack.main as spack_main  # noqa: PLC0415

        ok = 0
        failed = 0
        known_overflow_failure_count = 0
        for pkg in packages:
            print(f"  pre-solving {pkg} …", flush=True)
            captured_out = io.StringIO()
            captured_err = io.StringIO()
            _old_stdout = sys.stdout
            _old_stderr = sys.stderr
            try:
                sys.stdout = captured_out
                sys.stderr = captured_err
                rc = spack_main.main(["spec", pkg])
            except Exception as exc:  # noqa: BLE001
                rc = 1
                captured_err.write(str(exc))
            finally:
                sys.stdout = _old_stdout
                sys.stderr = _old_stderr

            if rc in (0, None):
                ok += 1
                print(f"    ✓ {pkg}", flush=True)
            else:
                failed += 1
                err_text = captured_err.getvalue().strip()
                if _KNOWN_OVERFLOW_ERROR in err_text:
                    known_overflow_failure_count += 1
                print(
                    f"    ✗ {pkg} (rc={rc})"
                    + (f": {err_text[:200]}" if err_text else ""),
                    flush=True,
                )

        print(
            f"  Pre-solve complete: {ok} cached, {failed} failed.",
            flush=True,
        )
        exit_code = 0 if failed == 0 else 2
        # Only tolerate this specific class when *every* failure matches it;
        # if any other error appears we keep the build failure behavior.
        if failed > 0 and failed == known_overflow_failure_count:
            print(
                "  WARNING: pre-solve failures matched known integer-overflow "
                "errors in Spack concretization; keeping partial cache.",
                flush=True,
            )
            exit_code = 0
        return exit_code

    finally:
        shutil.rmtree(home_dir, ignore_errors=True)


def _write(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


if __name__ == "__main__":
    sys.exit(main())
