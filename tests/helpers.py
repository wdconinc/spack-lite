"""
helpers.py — shared test utilities for spack-lite tests.

Importable from both test modules and conftest.py.
"""

import os
import subprocess
import sys

# Path to the standalone Pyodide-environment runner
RUNNER = os.path.join(os.path.dirname(__file__), "pyodide_runner.py")


def run_in_shell(command, *, timeout=60, extra_env=None):
    """Run *command* in the simulated Pyodide shell environment.

    Spawns a fresh subprocess that applies the shim_system.py monkey-patches
    and then calls run_shell_command(*command*).  Using a subprocess keeps the
    subprocess mock isolated so pytest's own process management is unaffected.

    Returns a :class:`subprocess.CompletedProcess` whose *stdout* and *stderr*
    attributes are strings.
    """
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, RUNNER, command],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )
