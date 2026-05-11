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
        for _fname in ("config.yaml", "concretizer.yaml", "packages.yaml", "repos.yaml"):
            _src = os.path.join(_cfg_src, _fname)
            if os.path.isfile(_src):
                shutil.copy(_src, os.path.join(_cfg_tmp, _fname))
        _compilers_src = os.path.join(_cfg_src, "compilers.yaml")
        if os.path.isfile(_compilers_src):
            shutil.copy(_compilers_src, os.path.join(_linux_cfg_dir, "compilers.yaml"))

    os.environ["SPACK_USER_CONFIG_PATH"] = _cfg_tmp

# ---------------------------------------------------------------------------
# Optionally simulate Pyodide's lzma-unavailable environment.
#
# When SPACK_LITE_MOCK_LZMA_UNAVAILABLE=1 is set, lzma is removed from
# sys.modules so that shim_system.py's `except ImportError:` block runs,
# exactly as it would in Pyodide where lzma is not part of the stdlib.
# ---------------------------------------------------------------------------
if os.environ.get("SPACK_LITE_MOCK_LZMA_UNAVAILABLE"):
    sys.modules["lzma"] = None  # `import lzma` raises ImportError when None

# ---------------------------------------------------------------------------
# Optionally simulate the Pyodide/WASM environment constraints.
#
# When SPACK_LITE_SIMULATE_PYODIDE=1 is set, three constraints that are
# present in a real Pyodide WASM runtime are applied before the shim runs:
#
#  (a) A stub ``js`` module is installed so that _is_pyodide() in
#      shim_system.py returns True, triggering the serial ProcessPoolExecutor
#      fallback unconditionally (rather than waiting for the first failure).
#
#  (b) The ``_multiprocessing`` C extension is blocked, exactly as Pyodide
#      removes it.  shim_system.py's section 13 installs the threading-backed
#      SemLock stub in response.
#
#  (c) ``os.pipe()`` is patched to raise OSError(ENOSYS=52), reproducing the
#      Emscripten WASM restriction.  shim_system.py's section 15 installs the
#      queue-backed _QueueConnection in response.
#
#  (d) ``threading.Thread.start`` is patched to raise the
#      "thread constructor failed" RuntimeError after a configurable number of
#      successful starts (default: 0 extra threads, i.e. all new thread starts
#      fail).  This exercises the runtime fallback in _ResilientProcessPoolExecutor
#      and exposes any code path that creates threads outside the patched executor.
#
# The combination of (a)–(d) faithfully mimics the thread-starved Pyodide
# environment without requiring a real browser.
# ---------------------------------------------------------------------------
if os.environ.get("SPACK_LITE_SIMULATE_PYODIDE"):
    import builtins
    import types
    import threading as _sim_threading

    # (a) Stub js module — makes _is_pyodide() return True in shim_system.py.
    sys.modules["js"] = types.ModuleType("js")

    # (b) Block _multiprocessing so shim_system.py installs the SemLock stub.
    _real_import = builtins.__import__

    def _blocking_import(name, *args, **kwargs):
        if name == "_multiprocessing" and "_multiprocessing" not in sys.modules:
            raise ImportError(
                "The module '_multiprocessing' is removed from the Python "
                "standard library in the Pyodide distribution due to browser "
                "limitations."
            )
        return _real_import(name, *args, **kwargs)

    builtins.__import__ = _blocking_import
    for _key in list(sys.modules.keys()):
        if "multiprocessing" in _key or _key == "_multiprocessing":
            del sys.modules[_key]

    # (c) Patch os.pipe() to raise ENOSYS (errno 52) — Emscripten restriction.
    def _fake_pipe():
        raise OSError(52, "Function not implemented")

    os.pipe = _fake_pipe  # noqa: E731

    # (d) Patch threading.Thread.start to raise the thread-constructor error so
    #     that any code creating threads outside the patched ProcessPoolExecutor
    #     is caught.  Allow _PYODIDE_SIM_EXTRA_THREADS extra starts to succeed
    #     first (default 0: all new thread starts fail immediately).
    _extra_allowed = int(os.environ.get("SPACK_LITE_SIMULATE_PYODIDE_EXTRA_THREADS", "0"))
    _sim_thread_budget = [_extra_allowed]
    _real_thread_start = _sim_threading.Thread.start

    def _fail_thread_start(self):
        if _sim_thread_budget[0] > 0:
            _sim_thread_budget[0] -= 1
            return _real_thread_start(self)
        raise RuntimeError(
            "thread constructor failed: Resource temporarily unavailable"
        )

    _sim_threading.Thread.start = _fail_thread_start

    # (e) Patch clingo.Control.solve via importlib.import_module interception so
    #     that any call with async_=True raises the thread-constructor error —
    #     mimicking Pyodide's inability to spawn C-level threads (pthread_create →
    #     EAGAIN).  spack.solver.core.clingo() loads the clingo package via
    #     importlib.import_module (not via "import clingo"), so we wrap that
    #     function rather than builtins.__import__ to reliably intercept the load.
    #
    #     Guard: only patch when the resolved module IS the external clingo package
    #     (has Control attribute), not spack.bootstrap.clingo (a spack-internal
    #     module that shares the bare name "clingo" in relative imports).
    import importlib as _sim_importlib
    _clingo_sim_patched = [False]
    _real_importlib_import_module = _sim_importlib.import_module

    def _clingo_patching_importlib(name, package=None):
        result = _real_importlib_import_module(name, package)
        if (
            name == "clingo"
            and not _clingo_sim_patched[0]
            and hasattr(result, "Control")
        ):
            _clingo_sim_patched[0] = True
            _real_control_solve = result.Control.solve

            def _fail_async_clingo_solve(ctrl_self, *a, **kw):
                if kw.get("async_", False):
                    raise RuntimeError(
                        "thread constructor failed: Resource temporarily unavailable"
                    )
                return _real_control_solve(ctrl_self, *a, **kw)

            result.Control.solve = _fail_async_clingo_solve
        return result

    _sim_importlib.import_module = _clingo_patching_importlib

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

    if sys.argv[1] == "--multi":
        # Multi-command mode: remaining args are JSON-encoded list of commands.
        # All commands share the same Python environment (same Spack session),
        # allowing cross-command state (e.g., cache invalidation) to be tested.
        # Output: newline-delimited JSON, one record per command.
        _commands = json.loads(sys.argv[2])
        for _cmd in _commands:
            _data = json.loads(run_shell_command(_cmd))  # noqa: F821
            sys.stdout.write(json.dumps(_data) + "\n")
        sys.exit(0)

    _command = " ".join(sys.argv[1:])
    _data = json.loads(run_shell_command(_command))  # noqa: F821 — defined by exec
    _output = _data.get("output", "")
    sys.stdout.write(_output)
    sys.exit(0)
