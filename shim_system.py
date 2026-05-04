"""
shim_system.py — The "System Lie"

This module monkey-patches the Python standard library so that Spack
believes it is running on a standard Linux/x86_64 host even though the
actual runtime is a Pyodide/Emscripten WebAssembly environment inside a
browser where fork(2), exec(2), and most POSIX syscalls are unavailable.

Load this module *before* importing any Spack code:

    exec(open('shim_system.py').read())

or, in a Pyodide worker:

    pyodide.runPythonAsync(open('shim_system.py').read())
"""

import sys
import os
import platform
import collections

# ---------------------------------------------------------------------------
# 1.  Platform identity — make Spack detect "Linux x86_64"
# ---------------------------------------------------------------------------
sys.platform = "linux"
os.name = "posix"


# os.uname() replacement
class _FakeUname:
    """Mimics the named-tuple returned by the real os.uname()."""

    sysname = "Linux"
    nodename = "spack-browser"
    release = "5.15.0-generic"
    version = "#1 SMP Mon Jan 01 00:00:00 UTC 2024"
    machine = "x86_64"

    def __iter__(self):
        return iter(
            [self.sysname, self.nodename, self.release, self.version, self.machine]
        )

    def __getitem__(self, idx):
        return list(self)[idx]

    def __repr__(self):
        return (
            f"posix.uname_result("
            f"sysname={self.sysname!r}, nodename={self.nodename!r}, "
            f"release={self.release!r}, version={self.version!r}, "
            f"machine={self.machine!r})"
        )


os.uname = lambda: _FakeUname()

# platform module
platform.machine = lambda: "x86_64"
platform.system = lambda: "Linux"
platform.release = lambda: "5.15.0-generic"
platform.node = lambda: "spack-browser"
platform.processor = lambda: "x86_64"
platform.architecture = lambda bits="", linkage="": ("64bit", "ELF")

_UnameTuple = collections.namedtuple(
    "uname_result",
    ["system", "node", "release", "version", "machine", "processor"],
)
platform.uname = lambda: _UnameTuple(
    "Linux", "spack-browser", "5.15.0-generic", "#1 SMP", "x86_64", "x86_64"
)

# ---------------------------------------------------------------------------
# 2.  subprocess shim — intercept all shell-out calls
# ---------------------------------------------------------------------------
import subprocess  # noqa: E402 — must come after platform patching
from unittest.mock import MagicMock  # noqa: E402


def _build_mock_result(args=None, **kwargs):
    """Return a Mock CompletedProcess-like object with sensible stdout."""
    cmd = " ".join(map(str, args)) if isinstance(args, (list, tuple)) else str(args or "")

    stdout = b""
    stderr = b""

    if "uname" in cmd:
        stdout = b"x86_64\n"
    elif "gcc" in cmd or ("cc" in cmd and "clang" not in cmd):
        stdout = b"gcc (Ubuntu 11.4.0-1ubuntu1~22.04) 11.4.0\n"
    elif "g++" in cmd or "c++" in cmd:
        stdout = b"g++ (Ubuntu 11.4.0-1ubuntu1~22.04) 11.4.0\n"
    elif "gfortran" in cmd or "fortran" in cmd:
        stdout = b"GNU Fortran (Ubuntu 11.4.0-1ubuntu1~22.04) 11.4.0\n"
    elif "git" in cmd:
        if "rev-parse" in cmd or "log" in cmd:
            stdout = b"abc1234\n"
        else:
            stdout = b"git version 2.34.1\n"
    elif "lscpu" in cmd:
        stdout = (
            b"Architecture: x86_64\n"
            b"CPU(s): 4\n"
            b"Thread(s) per core: 2\n"
            b"Core(s) per socket: 2\n"
            b"Socket(s): 1\n"
            b"Vendor ID: GenuineIntel\n"
            b"CPU family: 6\n"
            b"Model: 165\n"
            b"Model name: Intel(R) Core(TM) i7 CPU\n"
        )
    elif "clingo" in cmd:
        stdout = b"clingo version 5.6.2\n"
    elif "module" in cmd:
        # Environment-modules / Lmod — not available in browser
        stdout = b""
    elif "id" in cmd and len(cmd.strip()) <= 4:
        stdout = b"uid=1000(pyodide) gid=1000(pyodide) groups=1000(pyodide)\n"
    elif "make" in cmd:
        stdout = b"GNU Make 4.3\n"
    elif "cmake" in cmd:
        stdout = b"cmake version 3.22.1\n"

    result = MagicMock()
    result.stdout = stdout
    result.stderr = stderr
    result.returncode = 0
    result.args = args
    return result


class _MockPopen:
    """Thin replacement for subprocess.Popen that never forks."""

    def __init__(self, args=None, *extra_positional, **kwargs):
        r = _build_mock_result(args)
        self._stdout = r.stdout
        self._stderr = r.stderr
        self.returncode = 0
        self.pid = 99999

    # Attributes expected by Spack / distutils
    stdout = None
    stderr = None

    def communicate(self, input=None, timeout=None):  # noqa: A002
        return self._stdout, self._stderr

    def wait(self, timeout=None):
        self.returncode = 0
        return 0

    def poll(self):
        return 0

    def kill(self):
        pass

    def terminate(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def _mock_run(args=None, *extra, **kwargs):
    return _build_mock_result(args)


def _mock_check_output(args=None, *extra, **kwargs):
    return _build_mock_result(args).stdout


subprocess.run = _mock_run
subprocess.call = lambda *a, **kw: 0
subprocess.check_call = lambda *a, **kw: 0
subprocess.check_output = _mock_check_output
subprocess.Popen = _MockPopen

# ---------------------------------------------------------------------------
# 3.  os.environ defaults — give Spack a sane environment
# ---------------------------------------------------------------------------
os.environ.setdefault("HOME", "/home/pyodide")
os.environ.setdefault("USER", "pyodide")
os.environ.setdefault("LOGNAME", "pyodide")
os.environ.setdefault("SHELL", "/bin/sh")
os.environ.setdefault("PATH", "/usr/bin:/bin:/usr/local/bin")
os.environ.setdefault("SPACK_USER_CONFIG_PATH", "/home/pyodide/.spack")
os.environ.setdefault("SPACK_ROOT", "/home/pyodide/spack")

# ---------------------------------------------------------------------------
# 4.  Ensure required filesystem structure exists
#     (Only attempt this when running inside Pyodide/MEMFS where we own /home)
# ---------------------------------------------------------------------------
_REQUIRED_DIRS = [
    "/home/pyodide/.spack/linux",
    "/home/pyodide/.spack/darwin",
    "/tmp/spack-stage",
    "/tmp/spack-cache",
]
for _d in _REQUIRED_DIRS:
    try:
        os.makedirs(_d, exist_ok=True)
    except (PermissionError, OSError):
        pass  # Silently skip on non-Pyodide hosts

# ---------------------------------------------------------------------------
# 5.  Patch grp / pwd modules (may not exist in Pyodide)
# ---------------------------------------------------------------------------
try:
    import grp  # noqa: F401
except ImportError:
    import types

    _grp = types.ModuleType("grp")

    class _grpstruct:
        gr_name = "pyodide"
        gr_passwd = "x"
        gr_gid = 1000
        gr_mem = []

    _grp.getgrnam = lambda name: _grpstruct()
    _grp.getgrgid = lambda gid: _grpstruct()
    _grp.getgrall = lambda: [_grpstruct()]
    sys.modules["grp"] = _grp

try:
    import pwd  # noqa: F401
except ImportError:
    import types

    _pwd = types.ModuleType("pwd")

    class _pwdstruct:
        pw_name = "pyodide"
        pw_passwd = "x"
        pw_uid = 1000
        pw_gid = 1000
        pw_gecos = "Pyodide User"
        pw_dir = "/home/pyodide"
        pw_shell = "/bin/sh"

    _pwd.getpwnam = lambda name: _pwdstruct()
    _pwd.getpwuid = lambda uid: _pwdstruct()
    _pwd.getpwall = lambda: [_pwdstruct()]
    sys.modules["pwd"] = _pwd

# ---------------------------------------------------------------------------
# 6.  Patch fcntl module (removed from Pyodide due to browser limitations)
# ---------------------------------------------------------------------------
try:
    import fcntl  # noqa: F401
except ImportError:
    import types

    _fcntl = types.ModuleType("fcntl")

    # Common constants used for file locking / descriptor flags
    _fcntl.LOCK_SH = 1
    _fcntl.LOCK_EX = 2
    _fcntl.LOCK_NB = 4
    _fcntl.LOCK_UN = 8
    _fcntl.F_GETFD = 1
    _fcntl.F_SETFD = 2
    _fcntl.F_GETFL = 3
    _fcntl.F_SETFL = 4
    _fcntl.FD_CLOEXEC = 1

    # No-op implementations — file locking is meaningless in a single-threaded
    # browser sandbox where concurrent access cannot occur.
    _fcntl.flock = lambda fd, operation: None
    _fcntl.fcntl = lambda fd, cmd, arg=0: 0
    _fcntl.ioctl = lambda fd, request, arg=0, mutate_flag=True: 0
    _fcntl.lockf = lambda fd, cmd, len=0, start=0, whence=0: None

    sys.modules["fcntl"] = _fcntl
