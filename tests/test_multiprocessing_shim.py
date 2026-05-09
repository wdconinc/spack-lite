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


# ---------------------------------------------------------------------------
# ProcessPoolExecutor / os.pipe shim tests (section 15)
# ---------------------------------------------------------------------------

# Preamble that replaces os.pipe with one that raises OSError(52) — the
# ENOSYS value used by musl libc in Emscripten/Pyodide — so the shim must
# install the queue-backed Pipe() fallback.
_PREAMBLE_OSPIPE = f"""\
import sys, os, builtins

_real_import = builtins.__import__

_BLOCKED = {{'_multiprocessing', '_posixshmem'}}

def _blocking_import(name, *args, **kwargs):
    if name in _BLOCKED and name not in sys.modules:
        raise ImportError("blocked: " + name)
    return _real_import(name, *args, **kwargs)

builtins.__import__ = _blocking_import

for _key in list(sys.modules.keys()):
    if 'multiprocessing' in _key or _key in _BLOCKED:
        del sys.modules[_key]

# Simulate musl/Emscripten where ENOSYS == 52
_real_pipe = os.pipe
def _fake_pipe():
    raise OSError(52, "Function not implemented")
os.pipe = _fake_pipe

exec(compile(open({_SHIM_PATH!r}).read(), {_SHIM_PATH!r}, "exec"))
"""


def _run_ospipe_shim_script(code: str, *, timeout: int = 30) -> subprocess.CompletedProcess:
    """Run *code* in a subprocess where ``os.pipe()`` raises OSError(52, ENOSYS)."""
    script = _PREAMBLE_OSPIPE + code
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


class TestOsPipeShim:
    """Section 15: multiprocessing.connection.Pipe is patched when os.pipe() is unavailable."""

    def test_mp_connection_pipe_patched(self):
        """multiprocessing.connection.Pipe must be replaced with the queue-backed fallback."""
        r = _run_ospipe_shim_script(
            "import multiprocessing.connection as mc\n"
            "print(mc.Pipe.__name__)\n"
        )
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == "_queue_Pipe"

    def test_process_pool_executor_construction(self):
        """ProcessPoolExecutor() must construct without raising OSError(52)."""
        r = _run_ospipe_shim_script(
            "import concurrent.futures\n"
            "executor = concurrent.futures.ProcessPoolExecutor(1)\n"
            "with executor:\n"
            "    pass\n"
            "print('ok')\n"
        )
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == "ok"

    def test_resource_tracker_semaphore_no_raise(self):
        """Creating a multiprocessing.Semaphore must not raise OSError(52) via resource_tracker.

        Without the resource-tracker no-op patch, SemLock.__init__ calls
        resource_tracker.register() which calls ensure_running() which calls os.pipe().
        This test verifies the shim prevents that failure.
        """
        r = _run_ospipe_shim_script(
            "import multiprocessing\n"
            "sem = multiprocessing.Semaphore(1)\n"
            "sem.acquire()\n"
            "sem.release()\n"
            "print('ok')\n"
        )
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == "ok"

    def test_queue_connection_send_recv(self):
        """The queue-backed connection can round-trip bytes (duplex=False)."""
        r = _run_ospipe_shim_script(
            "import multiprocessing.connection as mc\n"
            "r, w = mc.Pipe(duplex=False)\n"
            "w.send_bytes(b'hello')\n"
            "print(r.recv_bytes())\n"
        )
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == "b'hello'"

    def test_duplex_true_separate_channels(self):
        """duplex=True: each end has its own receive channel (no self-loopback)."""
        r = _run_ospipe_shim_script(
            "import multiprocessing.connection as mc\n"
            "a, b = mc.Pipe(duplex=True)\n"
            "a.send_bytes(b'from-a')\n"
            "b.send_bytes(b'from-b')\n"
            "# b receives what a sent, a receives what b sent\n"
            "print(b.recv_bytes())\n"
            "print(a.recv_bytes())\n"
        )
        assert r.returncode == 0, r.stderr
        lines = r.stdout.strip().splitlines()
        assert lines[0] == "b'from-a'"
        assert lines[1] == "b'from-b'"

    def test_recv_bytes_maxlength_exceeded(self):
        """recv_bytes(maxlength) must raise OSError when the message is too long."""
        r = _run_ospipe_shim_script(
            "import multiprocessing.connection as mc\n"
            "r, w = mc.Pipe(duplex=False)\n"
            "w.send_bytes(b'toolongmessage')\n"
            "try:\n"
            "    r.recv_bytes(maxlength=4)\n"
            "    print('no-error')\n"
            "except OSError:\n"
            "    print('raised')\n"
        )
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == "raised"

    def test_queue_connection_poll_empty(self):
        """poll() returns False on an empty connection."""
        r = _run_ospipe_shim_script(
            "import multiprocessing.connection as mc\n"
            "r, w = mc.Pipe(duplex=False)\n"
            "print(r.poll(0))\n"
        )
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == "False"

    def test_queue_connection_poll_after_send(self):
        """poll() returns True after send_bytes."""
        r = _run_ospipe_shim_script(
            "import multiprocessing.connection as mc\n"
            "r, w = mc.Pipe(duplex=False)\n"
            "w.send_bytes(b'data')\n"
            "print(r.poll(0))\n"
        )
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == "True"


# ---------------------------------------------------------------------------
# ProcessPoolExecutor thread-constructor fallback tests (section 16)
# ---------------------------------------------------------------------------

_PREAMBLE_THREAD_STARTUP_FAILURE = f"""\
import sys, os, builtins, threading

_real_import = builtins.__import__

_BLOCKED = {{'_multiprocessing', '_posixshmem'}}

def _blocking_import(name, *args, **kwargs):
    if name in _BLOCKED and name not in sys.modules:
        raise ImportError("blocked: " + name)
    return _real_import(name, *args, **kwargs)

builtins.__import__ = _blocking_import

for _key in list(sys.modules.keys()):
    if 'multiprocessing' in _key or _key in _BLOCKED:
        del sys.modules[_key]

# Simulate musl/Emscripten where ENOSYS == 52 for os.pipe()
def _fake_pipe():
    raise OSError(52, "Function not implemented")
os.pipe = _fake_pipe

# Simulate runtime thread creation failure in ProcessPoolExecutor setup.
_real_thread_start = threading.Thread.start
def _fail_thread_start(self):
    raise RuntimeError("thread constructor failed: Resource temporarily unavailable")
threading.Thread.start = _fail_thread_start

exec(compile(open({_SHIM_PATH!r}).read(), {_SHIM_PATH!r}, "exec"))
"""


def _run_threadfail_shim_script(code: str, *, timeout: int = 30) -> subprocess.CompletedProcess:
    """Run *code* where os.pipe and thread startup are both unavailable."""
    script = _PREAMBLE_THREAD_STARTUP_FAILURE + code
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


class TestProcessPoolThreadFailureFallback:
    """Section 16: ProcessPoolExecutor falls back to a serial executor."""

    def test_process_pool_executor_replaced(self):
        """ProcessPoolExecutor should be replaced when thread startup fails."""
        r = _run_threadfail_shim_script(
            "import concurrent.futures\n"
            "print(concurrent.futures.ProcessPoolExecutor.__name__)\n"
        )
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == "_SerialProcessPoolExecutor"

    def test_serial_executor_submit_returns_resolved_future(self):
        """submit() should execute work and return a resolved Future."""
        r = _run_threadfail_shim_script(
            "import concurrent.futures\n"
            "with concurrent.futures.ProcessPoolExecutor(1) as ex:\n"
            "    fut = ex.submit(pow, 2, 8)\n"
            "    print(fut.result())\n"
        )
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == "256"


# ---------------------------------------------------------------------------
# ProcessPoolExecutor Pyodide-environment fallback tests (section 16, case b)
#
# Scenario: the `js` module (Pyodide's JS bridge) is importable, signalling
# that we are running inside Pyodide/WASM where process forking is impossible
# and thread limits may be hit during concretisation.  The shim should replace
# ProcessPoolExecutor with the serial fallback unconditionally.
# ---------------------------------------------------------------------------

_PREAMBLE_PYODIDE_ENV = f"""\
import sys, os, builtins, types

_real_import = builtins.__import__

_BLOCKED = {{'_multiprocessing', '_posixshmem'}}

def _blocking_import(name, *args, **kwargs):
    if name in _BLOCKED and name not in sys.modules:
        raise ImportError("blocked: " + name)
    return _real_import(name, *args, **kwargs)

builtins.__import__ = _blocking_import

for _key in list(sys.modules.keys()):
    if 'multiprocessing' in _key or _key in _BLOCKED:
        del sys.modules[_key]

# Simulate musl/Emscripten where ENOSYS == 52 for os.pipe()
def _fake_pipe():
    raise OSError(52, "Function not implemented")
os.pipe = _fake_pipe

# Install a stub 'js' module — present only in Pyodide (its JS bridge).
# _is_pyodide() in shim_system.py detects this to apply the serial fallback
# even when thread startup did not fail at load time.
sys.modules['js'] = types.ModuleType('js')

exec(compile(open({_SHIM_PATH!r}).read(), {_SHIM_PATH!r}, "exec"))
"""


def _run_pyodide_env_shim_script(
    code: str, *, timeout: int = 30
) -> subprocess.CompletedProcess:
    """Run *code* in an environment that simulates Pyodide (js module present)."""
    script = _PREAMBLE_PYODIDE_ENV + code
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


class TestProcessPoolPyodideFallback:
    """Section 16 case (b): serial fallback when the Pyodide JS bridge is present."""

    def test_process_pool_executor_replaced_in_pyodide(self):
        """ProcessPoolExecutor should be _SerialProcessPoolExecutor in Pyodide."""
        r = _run_pyodide_env_shim_script(
            "import concurrent.futures\n"
            "print(concurrent.futures.ProcessPoolExecutor.__name__)\n"
        )
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == "_SerialProcessPoolExecutor"

    def test_submit_works_in_pyodide(self):
        """submit() should execute work and return a resolved Future in Pyodide."""
        r = _run_pyodide_env_shim_script(
            "import concurrent.futures\n"
            "with concurrent.futures.ProcessPoolExecutor(1) as ex:\n"
            "    fut = ex.submit(pow, 2, 8)\n"
            "    print(fut.result())\n"
        )
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == "256"

    def test_map_works_in_pyodide(self):
        """map() should work with the serial fallback in Pyodide."""
        r = _run_pyodide_env_shim_script(
            "import concurrent.futures\n"
            "with concurrent.futures.ProcessPoolExecutor(1) as ex:\n"
            "    results = list(ex.map(str, [1, 2, 3]))\n"
            "    print(results)\n"
        )
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == "['1', '2', '3']"
