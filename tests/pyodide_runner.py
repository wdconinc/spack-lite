#!/usr/bin/env python3
"""
Simulate the Pyodide shell environment in standard CPython.

Applies the shim_system.py monkey-patches (subprocess, os, platform, grp,
pwd, termios, …) and loads shell.py to expose run_shell_command(), then
executes the command supplied as a command-line argument and writes the
shell output to stdout.

Running this script in a separate subprocess is the recommended pattern for
tests that need to verify shell behaviour *with* the Pyodide shims active,
because the subprocess mock installed by shim_system.py would otherwise
interfere with pytest's own process management.

Usage (standalone):

    python tests/pyodide_runner.py "spack list"
    python tests/pyodide_runner.py "echo hello | grep hello"
    python tests/pyodide_runner.py "spack unit-test --help"

Environment variables:

    SPACK_ROOT              Path to the Spack installation tree
                            (default: /tmp/spack-src).
    SPACK_USER_CONFIG_PATH  Override for the Spack user-config directory
                            (default: a temporary directory populated from
                            the repo's spack_config/ files).
"""

import atexit
import json
import os
import shutil
import sys
import tempfile

# ---------------------------------------------------------------------------
# Locate repository root (this script lives in tests/)
# ---------------------------------------------------------------------------
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------------------
# Add Spack to sys.path before anything else is imported
# ---------------------------------------------------------------------------
_SPACK_ROOT = os.environ.get("SPACK_ROOT", "/tmp/spack-src")
for _lib in ("lib/spack", "lib/spack/external"):
    _p = os.path.join(_SPACK_ROOT, _lib)
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("SPACK_ROOT", _SPACK_ROOT)

# ---------------------------------------------------------------------------
# Set up a writable Spack user-config directory before the shim runs.
#
# shim_system.py calls os.environ.setdefault("SPACK_USER_CONFIG_PATH", …)
# so setting it here ensures the shim's default is not used.
# We copy the repo's spack_config/ files into a temporary directory so that
# the same fake-compiler + package configuration used in the browser is also
# active during tests.
# ---------------------------------------------------------------------------
if "SPACK_USER_CONFIG_PATH" not in os.environ:
    _cfg_tmp = tempfile.mkdtemp(prefix="spack-lite-test-cfg-")
    atexit.register(shutil.rmtree, _cfg_tmp, True)

    _linux_cfg_dir = os.path.join(_cfg_tmp, "linux")
    os.makedirs(_linux_cfg_dir, exist_ok=True)

    _cfg_src = os.path.join(_REPO_ROOT, "spack_config")
    if os.path.isdir(_cfg_src):
        for _fname in ("config.yaml", "packages.yaml"):
            _src = os.path.join(_cfg_src, _fname)
            if os.path.isfile(_src):
                shutil.copy(_src, os.path.join(_cfg_tmp, _fname))
        _compilers_src = os.path.join(_cfg_src, "compilers.yaml")
        if os.path.isfile(_compilers_src):
            shutil.copy(_compilers_src, os.path.join(_linux_cfg_dir, "compilers.yaml"))

    os.environ["SPACK_USER_CONFIG_PATH"] = _cfg_tmp

# ---------------------------------------------------------------------------
# Apply system shims
# (monkey-patches subprocess, os, platform, grp, pwd, termios, tty, …)
#
# shim_system.py is exec'd rather than imported as a module because it is
# designed to run as a script that modifies the *current* process's module
# globals (e.g. ``subprocess.run = _mock_run``).  A normal import would
# isolate those assignments inside the shim module's own namespace, leaving
# the calling module's ``subprocess`` reference untouched.  The exec approach
# is the documented usage pattern described in shim_system.py's own docstring.
# ---------------------------------------------------------------------------
_SHIM_PATH = os.path.join(_REPO_ROOT, "shim_system.py")
with open(_SHIM_PATH) as _fh:
    exec(compile(_fh.read(), _SHIM_PATH, "exec"))  # noqa: S102

# ---------------------------------------------------------------------------
# Load the Python-backed shell
# (defines run_shell_command at module global scope via exec)
#
# shell.py is exec'd so that run_shell_command() is deposited directly into
# this module's global namespace where the __main__ block can reference it
# without an attribute lookup on an intermediate module object.  The same
# pattern is used by worker.js (runPythonAsync) in the browser environment.
# ---------------------------------------------------------------------------
_SHELL_PATH = os.path.join(_REPO_ROOT, "shell.py")
with open(_SHELL_PATH) as _fh:
    exec(compile(_fh.read(), _SHELL_PATH, "exec"))  # noqa: S102

# run_shell_command() is now part of this module's global namespace.

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: pyodide_runner.py <shell command>", file=sys.stderr)
        sys.exit(2)

    _command = " ".join(sys.argv[1:])
    _data = json.loads(run_shell_command(_command))  # noqa: F821 — defined by exec
    _output = _data.get("output", "")
    sys.stdout.write(_output)
    sys.exit(0)
