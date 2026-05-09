"""
helpers.py — shared test utilities for spack-lite tests.

Importable from both test modules and conftest.py.
"""

import json
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


def run_multi_in_shell(commands, *, timeout=300, extra_env=None):
    """Run *commands* sequentially in a single shared shell session.

    Unlike multiple :func:`run_in_shell` calls (each spawns a fresh
    subprocess), all commands here share one Python/Spack environment.  This
    allows cross-command state — such as Spack's package-repo cache — to be
    tested.

    Returns a ``(records, proc)`` tuple where *records* is a list of dicts
    (one per command, each with keys ``"output"`` and ``"cwd"``) and *proc*
    is the :class:`subprocess.CompletedProcess` for the runner itself.
    """
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    proc = subprocess.run(
        [sys.executable, RUNNER, "--multi", json.dumps(commands)],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )
    records = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records, proc
