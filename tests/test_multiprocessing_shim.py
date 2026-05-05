"""
test_multiprocessing_shim.py — regression tests for the _multiprocessing shim.

Verifies that shim_system.py's section 13 stub for _multiprocessing lets
``import multiprocessing`` and related primitives work correctly when the C
extension is absent (as it is in Pyodide).

Each test runs the verification code in a fresh subprocess that:
  1. Blocks _multiprocessing to simulate the Pyodide environment.
  2. Clears any cached multiprocessing modules.
  3. Execs shim_system.py (the same path used by pyodide_runner.py).
  4. Exercises the primitives and prints a result line.

Running in a subprocess keeps the import side-effects from the blocking
import hook isolated from the rest of the test suite.
"""

import os
import subprocess
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SHIM_PATH = os.path.join(_REPO_ROOT, "shim_system.py")

# Boilerplate that every test subprocess runs before the test-specific code.
# The hook only blocks _multiprocessing when it is not yet in sys.modules so
# that after the shim installs the stub, subsequent imports find it normally.
_PREAMBLE = f"""\
import sys
import builtins

_real_import = builtins.__import__

def _blocking_import(name, *args, **kwargs):
    if name == '_multiprocessing' and '_multiprocessing' not in sys.modules:
        raise ImportError(
            "The module '_multiprocessing' is removed from the Python "
            "standard library in the Pyodide distribution due to browser "
            "limitations."
        )
    return _real_import(name, *args, **kwargs)

builtins.__import__ = _blocking_import

for _key in list(sys.modules.keys()):
    if 'multiprocessing' in _key or _key == '_multiprocessing':
        del sys.modules[_key]

exec(compile(open({_SHIM_PATH!r}).read(), {_SHIM_PATH!r}, "exec"))
"""


def _run_shim_script(code: str, *, timeout: int = 30) -> subprocess.CompletedProcess:
    """Run *code* in a subprocess where ``_multiprocessing`` is blocked.

    The subprocess applies shim_system.py first (which registers the stub),
    then executes *code*.  Returns the completed process so callers can assert
    on stdout/returncode.
    """
    script = _PREAMBLE + code
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


class TestMultiprocessingShimImport:
    """The _multiprocessing stub is registered so imports succeed."""

    def test_import_multiprocessing(self):
        """``import multiprocessing`` must succeed under the shim."""
        r = _run_shim_script("import multiprocessing; print('ok')")
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == "ok"

    def test_import_synchronize(self):
        """``import multiprocessing.synchronize`` must succeed under the shim."""
        r = _run_shim_script(
            "import multiprocessing.synchronize; print('ok')"
        )
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == "ok"

    def test_stub_registered_in_sys_modules(self):
        """The stub must be present in sys.modules['_multiprocessing']."""
        r = _run_shim_script(
            "import sys; print('_multiprocessing' in sys.modules)"
        )
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == "True"


class TestMultiprocessingPrimitives:
    """Synchronization primitives work under the shim."""

    def test_lock_acquire_release(self):
        """multiprocessing.Lock() can be acquired and released."""
        r = _run_shim_script(
            "import multiprocessing\n"
            "lock = multiprocessing.Lock()\n"
            "lock.acquire()\n"
            "lock.release()\n"
            "print('ok')\n"
        )
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == "ok"

    def test_lock_context_manager(self):
        """multiprocessing.Lock() works as a context manager."""
        r = _run_shim_script(
            "import multiprocessing\n"
            "lock = multiprocessing.Lock()\n"
            "with lock:\n"
            "    print('inside')\n"
            "print('outside')\n"
        )
        assert r.returncode == 0, r.stderr
        assert "inside" in r.stdout
        assert "outside" in r.stdout

    def test_semaphore_acquire_release(self):
        """multiprocessing.Semaphore(3) can be acquired and released."""
        r = _run_shim_script(
            "import multiprocessing\n"
            "sem = multiprocessing.Semaphore(3)\n"
            "sem.acquire()\n"
            "sem.release()\n"
            "print('ok')\n"
        )
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == "ok"

    def test_semaphore_non_blocking(self):
        """Semaphore.acquire(block=False) returns True when the semaphore is available."""
        r = _run_shim_script(
            "import multiprocessing\n"
            "sem = multiprocessing.Semaphore(1)\n"
            "result = sem.acquire(block=False)\n"
            "print(result)\n"
        )
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == "True"

    def test_bounded_semaphore(self):
        """multiprocessing.BoundedSemaphore value tracking works correctly."""
        r = _run_shim_script(
            "import multiprocessing\n"
            "bsem = multiprocessing.BoundedSemaphore(2)\n"
            "bsem.acquire()\n"
            "bsem.acquire()\n"
            "result = bsem.acquire(block=False)\n"
            "print(result)\n"
        )
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == "False"

    def test_cpu_count(self):
        """multiprocessing.cpu_count() returns a positive integer."""
        r = _run_shim_script(
            "import multiprocessing\n"
            "n = multiprocessing.cpu_count()\n"
            "print(isinstance(n, int) and n > 0)\n"
        )
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == "True"


# ---------------------------------------------------------------------------
# _posixshmem shim tests (section 14)
# ---------------------------------------------------------------------------

# Preamble that blocks both _multiprocessing and _posixshmem so the shim
# must install stubs for both — exactly as Pyodide does.
_PREAMBLE_BOTH = f"""\
import sys
import builtins

_real_import = builtins.__import__

_BLOCKED = {{'_multiprocessing', '_posixshmem'}}

def _blocking_import(name, *args, **kwargs):
    if name in _BLOCKED and name not in sys.modules:
        raise ImportError(
            "The module '" + name + "' is removed from the Python "
            "standard library in the Pyodide distribution due to browser "
            "limitations."
        )
    return _real_import(name, *args, **kwargs)

builtins.__import__ = _blocking_import

for _key in list(sys.modules.keys()):
    if 'multiprocessing' in _key or _key in _BLOCKED:
        del sys.modules[_key]

exec(compile(open({_SHIM_PATH!r}).read(), {_SHIM_PATH!r}, "exec"))
"""


def _run_shim_script_both(code: str, *, timeout: int = 30) -> subprocess.CompletedProcess:
    """Run *code* in a subprocess where both ``_multiprocessing`` and
    ``_posixshmem`` are blocked to simulate the full Pyodide environment.
    """
    script = _PREAMBLE_BOTH + code
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


class TestPosixShMemShimImport:
    """The _posixshmem stub lets shared-memory-related imports succeed."""

    def test_posixshmem_in_sys_modules(self):
        """_posixshmem must be registered in sys.modules after the shim runs."""
        r = _run_shim_script_both(
            "import sys; print('_posixshmem' in sys.modules)"
        )
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == "True"

    def test_import_shared_memory(self):
        """``import multiprocessing.shared_memory`` must succeed under the shim."""
        r = _run_shim_script_both(
            "import multiprocessing.shared_memory; print('ok')"
        )
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == "ok"

    def test_import_managers(self):
        """``import multiprocessing.managers`` must succeed under the shim."""
        r = _run_shim_script_both(
            "import multiprocessing.managers; print('ok')"
        )
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == "ok"

    def test_shm_open_raises_oserror(self):
        """_posixshmem.shm_open must raise OSError with errno.ENOSYS."""
        r = _run_shim_script_both(
            "import _posixshmem, os, errno\n"
            "try:\n"
            "    _posixshmem.shm_open('/test', os.O_CREAT | os.O_RDWR, 0o600)\n"
            "    print('no-error')\n"
            "except OSError as e:\n"
            "    print(e.errno == errno.ENOSYS)\n"
        )
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == "True"

    def test_shm_unlink_raises_oserror(self):
        """_posixshmem.shm_unlink must raise OSError with errno.ENOSYS."""
        r = _run_shim_script_both(
            "import _posixshmem, errno\n"
            "try:\n"
            "    _posixshmem.shm_unlink('/test')\n"
            "    print('no-error')\n"
            "except OSError as e:\n"
            "    print(e.errno == errno.ENOSYS)\n"
        )
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == "True"

    def test_multiprocessing_managers_still_loads_locks(self):
        """multiprocessing.Lock() still works when both shims are active."""
        r = _run_shim_script_both(
            "import multiprocessing\n"
            "lock = multiprocessing.Lock()\n"
            "with lock:\n"
            "    print('inside')\n"
        )
        assert r.returncode == 0, r.stderr
        assert "inside" in r.stdout

